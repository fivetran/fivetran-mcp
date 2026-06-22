#!/usr/bin/env python3
"""Split OpenAPI schema into minimal per-endpoint files.

Takes the full OpenAPI spec and produces one lightweight file per endpoint,
containing only what an agent needs to call the tool:
  - description/summary
  - path and method
  - path/query parameters (no headers)
  - request body schema (with $refs resolved inline)

Response schemas, components, examples, and other metadata are stripped.

Usage:
    python split_openapi_by_endpoint.py <input_file> <output_dir>

Example:
    python split_openapi_by_endpoint.py fivetran-open-api-definition.json open-api-definitions
"""

import json
import re
from collections import defaultdict
from pathlib import Path


def resolve_ref(ref: str, components: dict) -> dict | None:
    """Resolve a $ref string to its component schema."""
    if not ref.startswith('#/components/'):
        return None
    parts = ref[len('#/components/'):].split('/')
    if len(parts) != 2:
        return None
    component_type, component_name = parts
    return components.get(component_type, {}).get(component_name)


def resolve_refs_inline(obj, components: dict):
    """Recursively resolve all $ref values inline, returning a new object."""
    if isinstance(obj, dict):
        if '$ref' in obj:
            resolved = resolve_ref(obj['$ref'], components)
            if resolved:
                return resolve_refs_inline(resolved, components)
            return obj
        return {k: resolve_refs_inline(v, components) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_refs_inline(item, components) for item in obj]
    return obj


def strip_examples(obj):
    """Recursively remove 'example' and 'examples' keys to reduce size."""
    if isinstance(obj, dict):
        return {
            k: strip_examples(v) for k, v in obj.items()
            if k not in ('example', 'examples')
        }
    elif isinstance(obj, list):
        return [strip_examples(item) for item in obj]
    return obj


def extract_parameters(operation: dict) -> list[dict]:
    """Extract path and query parameters, skipping headers."""
    params = []
    for param in operation.get('parameters', []):
        if param.get('in') in ('path', 'query'):
            clean_param = {
                'name': param['name'],
                'in': param['in'],
                'required': param.get('required', False),
            }
            if 'description' in param:
                clean_param['description'] = param['description']
            if 'schema' in param:
                schema = {k: v for k, v in param['schema'].items()
                          if k not in ('example', 'examples')}
                clean_param['schema'] = schema
            params.append(clean_param)
    return params


def extract_request_body(operation: dict, components: dict) -> dict | None:
    """Extract and resolve the request body schema, stripping examples."""
    request_body = operation.get('requestBody')
    if not request_body:
        return None

    content = request_body.get('content', {})
    json_content = content.get('application/json', {})
    schema = json_content.get('schema')
    if not schema:
        return None

    resolved = resolve_refs_inline(schema, components)
    return strip_examples(resolved)


def extract_endpoint_schema(openapi_doc: dict, path: str, method: str) -> dict:
    """Extract a minimal endpoint doc with only what's needed to call the API."""
    path_item = openapi_doc['paths'][path]
    operation = path_item[method]
    components = openapi_doc.get('components', {})

    endpoint_doc = {
        'description': operation.get('description', operation.get('summary', '')),
        'path': path,
        'method': method.upper(),
    }

    params = extract_parameters(operation)
    if params:
        endpoint_doc['parameters'] = params

    request_body = extract_request_body(operation, components)
    if request_body:
        endpoint_doc['request_body_schema'] = request_body

    return endpoint_doc


def get_resource_from_path(path: str) -> str:
    """Extract the resource name from an API path."""
    path = re.sub(r'^/v\d+/', '', path)
    parts = [p for p in path.split('/') if p and not p.startswith('{')]
    return parts[0] if parts else 'other'


def get_referenced_schema_files(server_file: Path) -> set[str]:
    """Return all schema_file paths already referenced in server.py (active or commented)."""
    pattern = re.compile(r'"schema_file":\s*"([^"]+)"')
    return {m.group(1) for m in pattern.finditer(server_file.read_text())}


def build_tool_entry(operation_id: str, schema_file_rel: str, endpoint_doc: dict) -> str:
    """Generate a commented-out TOOLS entry string for a new endpoint."""
    method = endpoint_doc['method']
    path = endpoint_doc['path']
    # Collapse multi-line descriptions and escape quotes for use in a Python string literal
    raw_desc = endpoint_doc.get('description', '')
    description = ' '.join(raw_desc.split()).replace('"', '\\"')

    if method == 'DELETE':
        description = f'⚠️ DESTRUCTIVE - Confirm with user before calling. {description}'
    elif method in ('POST', 'PATCH', 'PUT'):
        description = f'⚠️ WRITE OPERATION - Confirm with user before calling. {description}'

    path_params = [p['name'] for p in endpoint_doc.get('parameters', []) if p.get('in') == 'path']
    has_body = 'request_body_schema' in endpoint_doc or method in ('POST', 'PATCH', 'PUT')
    params = path_params + (['request_body'] if has_body else [])
    auto_paginate = method == 'GET' and operation_id.startswith('list_')

    lines = [f'    # "{operation_id}": {{']
    lines.append(f'    #     "description": "{description}",')
    lines.append(f'    #     "schema_file": "{schema_file_rel}",')
    lines.append(f'    #     "method": "{method}",')
    lines.append(f'    #     "endpoint": "{path}",')
    if params:
        lines.append(f'    #     "params": {json.dumps(params)},')
    if auto_paginate:
        lines.append(f'    #     "auto_paginate": True,')
    lines.append(f'    # }},')
    return '\n'.join(lines)


def inject_new_tools(output_dir: Path, all_mappings: dict, server_file: Path) -> None:
    """Detect schema files not yet in server.py and inject commented-out tool entries."""
    referenced = get_referenced_schema_files(server_file)
    lines = server_file.read_text().splitlines()

    SEP = '    # ' + '=' * 76

    # Map section title -> line index of its first separator line
    sections = {}
    for i in range(len(lines) - 2):
        if lines[i] == SEP and lines[i + 2] == SEP:
            title = lines[i + 1].strip().lstrip('#').strip()
            sections[title] = i

    # Find line index of the TOOLS dict closing brace
    tools_start = next(i for i, l in enumerate(lines) if l.startswith('TOOLS = {'))
    tools_end = next(i for i in range(tools_start, len(lines)) if lines[i].rstrip() == '}')

    # Collect new tools grouped by resource
    new_by_resource: dict[str, list] = {}
    for resource_name, endpoint_mapping in all_mappings.items():
        for operation_id, info in endpoint_mapping.items():
            schema_file_rel = f"{output_dir.name}/{info['file'].replace(chr(92), '/')}"
            if schema_file_rel in referenced:
                continue
            schema_path = output_dir / info['file']
            if not schema_path.exists():
                continue
            with open(schema_path) as f:
                endpoint_doc = json.load(f)
            new_by_resource.setdefault(resource_name, []).append(
                (operation_id, schema_file_rel, endpoint_doc)
            )

    if not new_by_resource:
        print('\nserver.py is up to date — no new endpoints to add.')
        return

    # Build insertion map: line_index -> list of lines to insert before that line
    insertion_map: dict[int, list[str]] = defaultdict(list)

    for resource_name, tools in new_by_resource.items():
        section_title = resource_name.upper().replace('-', ' ')
        entry_lines = []
        for op_id, sf, doc in tools:
            entry_lines.extend(build_tool_entry(op_id, sf, doc).splitlines())

        if section_title in sections:
            # Find the next section separator after this section's header, or tools_end
            insert_at = tools_end
            for i in range(sections[section_title] + 3, tools_end):
                if lines[i] == SEP:
                    insert_at = i
                    break
            insertion_map[insert_at].extend(entry_lines)
        else:
            # New section: insert block before the TOOLS closing brace
            new_section = ['', SEP, f'    # {section_title}', SEP] + entry_lines
            insertion_map[tools_end].extend(new_section)

    # Apply insertions from bottom to top so earlier indices stay valid
    for insert_at in sorted(insertion_map.keys(), reverse=True):
        lines[insert_at:insert_at] = insertion_map[insert_at]

    server_file.write_text('\n'.join(lines) + '\n')

    total = sum(len(v) for v in new_by_resource.values())
    print(f'\nAdded {total} new tool entr{"y" if total == 1 else "ies"} to server.py (commented out):')
    for resource_name, tools in sorted(new_by_resource.items()):
        for op_id, _, _ in tools:
            print(f'  + {op_id} ({resource_name})')
    print('Uncomment entries in server.py to enable them.')


def main():
    import sys

    if len(sys.argv) != 3:
        print('Usage: python split_openapi_by_endpoint.py <input_file> <output_dir>')
        print('Example: python split_openapi_by_endpoint.py fivetran-open-api-definition.json open-api-definitions')
        return 1

    input_file = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])

    if not input_file.exists():
        print(f'Error: {input_file} not found')
        return 1

    # Clean out existing output directory so stale files don't linger
    if output_dir.exists():
        import shutil
        shutil.rmtree(output_dir)
        print(f'Cleaned existing {output_dir}/')
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f'Loading {input_file}...')
    with open(input_file) as f:
        openapi_doc = json.load(f)

    # Group endpoints by resource
    resources = {}
    for path, path_item in openapi_doc.get('paths', {}).items():
        resource = get_resource_from_path(path)
        if resource not in resources:
            resources[resource] = {'paths': {}, 'components': openapi_doc.get('components', {})}
        resources[resource]['paths'][path] = path_item

    print(f'Found {len(resources)} resources\n')

    all_mappings = {}
    total_original_lines = 0
    total_new_lines = 0

    for resource_name, resource_doc in sorted(resources.items()):
        print(f'Processing {resource_name}...')

        resource_openapi = {
            'paths': resource_doc['paths'],
            'components': resource_doc['components'],
        }

        resource_output_dir = output_dir / resource_name
        resource_output_dir.mkdir(parents=True, exist_ok=True)

        endpoint_mapping = {}

        for path, path_item in resource_doc['paths'].items():
            for method in ['get', 'post', 'put', 'patch', 'delete']:
                if method not in path_item:
                    continue

                operation = path_item[method]
                operation_id = operation.get('operationId')

                if not operation_id:
                    print(f'  WARNING: No operationId for {method.upper()} {path}, skipping')
                    continue

                endpoint_doc = extract_endpoint_schema(resource_openapi, path, method)

                output_file = resource_output_dir / f'{operation_id}.json'
                output_json = json.dumps(endpoint_doc, indent=2)
                with open(output_file, 'w') as f:
                    f.write(output_json)

                new_lines = output_json.count('\n') + 1
                total_new_lines += new_lines

                endpoint_mapping[operation_id] = {
                    'file': str(output_file.relative_to(output_dir)),
                    'path': path,
                    'method': method.upper(),
                    'summary': operation.get('summary', ''),
                }

                print(f'  Created: {operation_id}.json ({new_lines} lines)')

        all_mappings[resource_name] = endpoint_mapping
        print()

    # Write an index file
    index_file = output_dir / 'endpoint-index.json'
    with open(index_file, 'w') as f:
        json.dump(all_mappings, f, indent=2)
    print(f'Created endpoint index: {index_file}')

    total_endpoints = sum(len(m) for m in all_mappings.values())
    print(f'\nDone! Split into {total_endpoints} endpoint files across {len(all_mappings)} resources.')
    print(f'Total output: {total_new_lines} lines')

    # Inject any new endpoints into server.py
    server_file = Path(__file__).parent / 'server.py'
    if server_file.exists():
        inject_new_tools(output_dir, all_mappings, server_file)

    return 0


if __name__ == '__main__':
    exit(main())
