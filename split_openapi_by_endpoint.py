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

    return 0


if __name__ == '__main__':
    exit(main())
