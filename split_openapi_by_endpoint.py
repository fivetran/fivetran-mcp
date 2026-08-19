#!/usr/bin/env python3
"""Split OpenAPI schema into per-endpoint files.

Takes the full OpenAPI spec and produces one file per endpoint containing:
  - description (with ⚠️ warning prefix on write/delete methods)
  - path and method
  - deprecated flag (only if true)
  - path/query parameters (headers stripped)
  - request_body: required, description, and schemas keyed by content type
  - response: description and success (2xx) schemas keyed by content type

All $refs are resolved inline. Examples, tags, security, servers, non-JSON
component metadata, and error response schemas are stripped.

Usage:
    python split_openapi_by_endpoint.py <input_file> <output_dir>

Example:
    python split_openapi_by_endpoint.py fivetran-open-api-definition.json open-api-definitions
"""

import ast
import json
import re
from collections import defaultdict
from pathlib import Path


# Endpoints deliberately excluded from the manifest. `create_system_key` and
# `rotate_system_key` mint credentials that outlive the session — a class of
# privilege escalation that no agent workflow needs and every FIVETRAN_SCOPES
# grant should refuse to authorize. Discovery, get_schema, and call all skip
# them because they never make it into endpoints.json.
EXCLUDED_ENDPOINTS = frozenset({"create_system_key", "rotate_system_key"})


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


def strip_discriminator_mappings(obj):
    """Remove discriminator blocks; hoist mapping keys onto the propertyName property as enum.

    Per the OpenAPI spec a discriminator is only meaningful alongside oneOf/anyOf/allOf,
    which none of these schemas have — and the mapping values are dangling refs into
    components (which we strip). The mapping keys are the one load-bearing piece: they
    enumerate valid values for the property named by propertyName.
    """
    if isinstance(obj, dict):
        disc = obj.get('discriminator')
        if isinstance(disc, dict) and isinstance(disc.get('mapping'), dict):
            pn = disc.get('propertyName')
            keys = sorted(disc['mapping'].keys())
            new = {
                k: strip_discriminator_mappings(v)
                for k, v in obj.items() if k != 'discriminator'
            }
            props = new.get('properties')
            if isinstance(props, dict) and pn in props and isinstance(props[pn], dict):
                target = props[pn]
                existing = target.get('enum')
                if existing is None:
                    target['enum'] = keys
                elif isinstance(existing, list) and set(existing) != set(keys):
                    print(
                        f"WARNING: discriminator mapping keys diverge from existing "
                        f"enum on property {pn!r}: "
                        f"mapping_only={sorted(set(keys) - set(existing))}, "
                        f"enum_only={sorted(set(existing) - set(keys))}"
                    )
            return new
        return {k: strip_discriminator_mappings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [strip_discriminator_mappings(item) for item in obj]
    return obj


def _merge_service_config(service: str, request_schema_name: str, components: dict) -> dict | None:
    """Assemble one per-service config file by pulling service-specific pieces out of
    `{service}_NewConnectorRequestV1` / `{service}_NewDestinationRequest`.

    Walks the request schema's `allOf` and keeps only refs whose target name is
    service-prefixed — dropping the shared base (`NewConnectorRequestV1`,
    `NewDestinationRequest`, `schema_format_schema_prefix`). Merges the kept parts'
    properties and required lists into a single flat object. For 32 connectors this
    also folds in `{service}_esm_keys_config_V1` so the agent gets one file per service.

    `components` is the top-level `spec['components']` object (with `schemas` inside),
    matching what `resolve_refs_inline` expects.
    """
    request = components.get('schemas', {}).get(request_schema_name)
    if not isinstance(request, dict) or 'allOf' not in request:
        return None

    merged_props: dict = {}
    merged_required: list[str] = []
    sources: list[str] = []

    for item in request.get('allOf', []):
        if not isinstance(item, dict):
            continue
        ref = item.get('$ref')
        if not ref:
            continue
        target = ref.split('/')[-1]
        if not target.startswith(f'{service}_'):
            continue
        resolved = resolve_refs_inline(item, components)
        if not isinstance(resolved, dict):
            continue
        sources.append(target)
        for prop_name, prop_schema in resolved.get('properties', {}).items():
            merged_props[prop_name] = prop_schema
        for req in resolved.get('required', []):
            if req not in merged_required:
                merged_required.append(req)

    if not merged_props:
        return None

    out: dict = {'type': 'object', 'properties': merged_props}
    if merged_required:
        out['required'] = merged_required
    out['x-sources'] = sources
    return strip_examples(out)


def write_service_configs(spec: dict, output_dir: Path) -> None:
    """Write one file per service to `_service-configs/{connectors,destinations}/{svc}.json`.

    Prerequisite for the get_schema(name, service=) router in the target architecture —
    lets a caller reach the actual config shape for a chosen service without splicing.
    """
    components = spec.get('components', {})
    schemas = components.get('schemas', {})
    if not schemas:
        print('  No components.schemas in spec; skipping service configs.')
        return

    connectors = sorted(
        n.removesuffix('_NewConnectorRequestV1')
        for n in schemas
        if n.endswith('_NewConnectorRequestV1')
    )
    destinations = sorted(
        n.removesuffix('_NewDestinationRequest')
        for n in schemas
        if n.endswith('_NewDestinationRequest')
    )

    base_dir = output_dir / '_service-configs'
    conn_dir = base_dir / 'connectors'
    dest_dir = base_dir / 'destinations'
    conn_dir.mkdir(parents=True, exist_ok=True)
    dest_dir.mkdir(parents=True, exist_ok=True)

    written = {'connectors': [], 'destinations': []}
    skipped = {'connectors': [], 'destinations': []}

    for svc in connectors:
        cfg = _merge_service_config(svc, f'{svc}_NewConnectorRequestV1', components)
        if cfg is None:
            skipped['connectors'].append(svc)
            continue
        (conn_dir / f'{svc}.json').write_text(json.dumps(cfg, indent=2))
        written['connectors'].append(svc)

    for svc in destinations:
        cfg = _merge_service_config(svc, f'{svc}_NewDestinationRequest', components)
        if cfg is None:
            skipped['destinations'].append(svc)
            continue
        (dest_dir / f'{svc}.json').write_text(json.dumps(cfg, indent=2))
        written['destinations'].append(svc)

    index = {'connectors': written['connectors'], 'destinations': written['destinations']}
    (base_dir / 'index.json').write_text(json.dumps(index, indent=2))

    print(
        f'  Wrote {len(written["connectors"])} connector configs, '
        f'{len(written["destinations"])} destination configs to _service-configs/'
    )
    for kind in ('connectors', 'destinations'):
        if skipped[kind]:
            print(f'  Skipped {len(skipped[kind])} {kind} (no service-specific allOf refs): '
                  f'{skipped[kind][:5]}{"..." if len(skipped[kind]) > 5 else ""}')


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
    """Extract request body: required flag, description, and schemas by content type."""
    request_body = operation.get('requestBody')
    if not request_body:
        return None

    content = request_body.get('content', {})
    schemas_by_type = {}
    for content_type, content_obj in content.items():
        schema = content_obj.get('schema')
        if schema:
            schemas_by_type[content_type] = strip_discriminator_mappings(
                strip_examples(resolve_refs_inline(schema, components))
            )

    if not schemas_by_type:
        return None

    result = {'content': schemas_by_type}
    if request_body.get('required'):
        result['required'] = True
    if 'description' in request_body:
        result['description'] = request_body['description']
    return result


def extract_response(operation: dict, components: dict) -> dict | None:
    """Extract success response schemas keyed by content type."""
    responses = operation.get('responses', {})
    success_response = responses.get('200') or responses.get('201')
    if not success_response:
        return None

    content = success_response.get('content', {})
    schemas_by_type = {}
    for content_type, content_obj in content.items():
        schema = content_obj.get('schema')
        if schema:
            schemas_by_type[content_type] = strip_discriminator_mappings(
                strip_examples(resolve_refs_inline(schema, components))
            )

    if not schemas_by_type:
        return None

    result = {'content': schemas_by_type}
    if 'description' in success_response:
        result['description'] = success_response['description']
    return result


def _response_is_paginated(operation: dict, components: dict) -> bool:
    """Return True if the success response schema has a next_cursor property under data."""
    responses = operation.get('responses', {})
    success = responses.get('200') or responses.get('201')
    if not success:
        return False
    for content_obj in success.get('content', {}).values():
        schema = resolve_refs_inline(content_obj.get('schema', {}), components)
        data_props = schema.get('properties', {}).get('data', {}).get('properties', {})
        if 'next_cursor' in data_props:
            return True
    return False


_LARGE_ENUM_STRIP_THRESHOLD = 50
_STRIP_RESPONSE_ENUM_PROPS = frozenset({"service"})


def _strip_large_enums_on_response(obj):
    """Recursively drop huge informational enums from response schemas.

    A large `service` enum on a response body constrains nothing — the API has
    already returned a specific value. It only balloons `get_schema` payloads.
    Applied only to whitelisted property names (`service`) so real domain enums
    like a small status list are untouched.
    """
    if isinstance(obj, dict):
        props = obj.get("properties")
        if isinstance(props, dict):
            for prop_name, prop_schema in props.items():
                if (
                    prop_name in _STRIP_RESPONSE_ENUM_PROPS
                    and isinstance(prop_schema, dict)
                    and isinstance(prop_schema.get("enum"), list)
                    and len(prop_schema["enum"]) > _LARGE_ENUM_STRIP_THRESHOLD
                ):
                    prop_schema.pop("enum", None)
        for v in obj.values():
            _strip_large_enums_on_response(v)
    elif isinstance(obj, list):
        for v in obj:
            _strip_large_enums_on_response(v)


def extract_endpoint_schema(openapi_doc: dict, path: str, method: str) -> dict:
    """Extract a minimal endpoint doc with only what's needed to call the API."""
    path_item = openapi_doc['paths'][path]
    operation = path_item[method]
    components = openapi_doc.get('components', {})

    method_upper = method.upper()
    description = operation.get('description', operation.get('summary', ''))
    if method_upper == 'DELETE':
        description = f'⚠️ DESTRUCTIVE - Confirm with user before calling. {description}'
    elif method_upper in ('POST', 'PATCH', 'PUT'):
        description = f'⚠️ WRITE OPERATION - Confirm with user before calling. {description}'
    if _response_is_paginated(operation, components):
        description = f'⚠️ RESULTS ARE PAGINATED. {description}'

    endpoint_doc = {
        'description': description,
        'path': path,
        'method': method_upper,
    }

    if operation.get('deprecated'):
        endpoint_doc['deprecated'] = True

    params = extract_parameters(operation)
    if params:
        endpoint_doc['parameters'] = params

    request_body = extract_request_body(operation, components)
    if request_body:
        endpoint_doc['request_body'] = request_body

    response = extract_response(operation, components)
    if response:
        _strip_large_enums_on_response(response)
        endpoint_doc['response'] = response

    return endpoint_doc


def get_resource_from_path(path: str) -> str:
    """Extract the resource name from an API path."""
    path = re.sub(r'^/v\d+/', '', path)
    parts = [p for p in path.split('/') if p and not p.startswith('{')]
    return parts[0] if parts else 'other'


def classify_scope(method: str) -> str:
    """Scope model: read (GET) / write (POST, PATCH) / delete (DELETE).

    No admin bucket — sensitive endpoints get protected by per-resource scoping
    in Step 5 (grants shaped like `connections:write`, `system-keys:delete`).
    """
    if method == 'GET':
        return 'read'
    if method == 'DELETE':
        return 'delete'
    return 'write'


def _summary_with_prefix(method: str, summary: str, description: str) -> str:
    """Short OpenAPI summary prefixed with an all-caps category word.

    Categories: DESTRUCTIVE (DELETE), WRITE OPERATION (POST/PATCH/PUT),
    PAGINATED (GETs whose response has a next_cursor). Non-paginated GETs get no prefix.
    """
    if method == 'DELETE':
        return f'DESTRUCTIVE - {summary}'
    if method in ('POST', 'PATCH', 'PUT'):
        return f'WRITE OPERATION - {summary}'
    if description.startswith('⚠️ RESULTS ARE PAGINATED'):
        return f'PAGINATED - {summary}'
    return summary


def _build_tools_index(entries: list[dict]) -> list[dict]:
    """One row per (resource, action) pair that has ≥1 non-deprecated endpoint.

    A pair whose every endpoint is deprecated (today: `certificates:write` — only
    `approve_certificate`, deprecated) gets no tool: it would exist solely to expose
    a dead endpoint. Tool name normalizes `-` to `_` so `system-keys` becomes
    `system_keys_delete` rather than `system-keys_delete`.
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in entries:
        groups[(e['resource'], e['scope'])].append(e)
    tools = []
    for (resource, action), members in groups.items():
        live = [m for m in members if not m.get('deprecated')]
        if not live:
            continue
        prefix = resource.replace('-', '_')
        tools.append({
            'name': f'{prefix}_{action}',
            'resource': resource,
            'action': action,
            'endpoints': len(live),
        })
    return sorted(tools, key=lambda t: t['name'])


def write_manifest(all_mappings: dict, output_dir: Path) -> list[dict]:
    """Emit endpoints.json — {tools, endpoints} for the resource:action router.

    `summary` is the short OpenAPI summary with an all-caps category prefix. Each
    entry also carries `tool_prefix` (resource with `-` → `_`) so the server can
    build tool names without re-normalizing. A top-level `tools` array lists every
    (resource, action) pair with ≥1 non-deprecated endpoint. Returns the tools list
    so callers can render adjacent artifacts (e.g. AVAILABLE_ACTIONS.md) without
    recomputing.
    """
    entries = []
    for resource in sorted(all_mappings):
        for name in sorted(all_mappings[resource]):
            info = all_mappings[resource][name]
            schema_rel = info['file']
            doc = json.loads((output_dir / schema_rel).read_text(encoding="utf-8"))
            entries.append({
                'name': name,
                'resource': resource,
                'tool_prefix': resource.replace('-', '_'),
                'method': info['method'],
                'path': info['path'],
                'summary': _summary_with_prefix(
                    info['method'], info.get('summary', ''), doc.get('description', '')
                ),
                'schema_file': schema_rel,
                'scope': classify_scope(info['method']),
                'deprecated': doc.get('deprecated', False),
            })

    tools = _build_tools_index(entries)

    (output_dir / 'endpoints.json').write_text(
        json.dumps({'tools': tools, 'endpoints': entries}, indent=2)
    )
    print(f'  Wrote endpoints.json with {len(entries)} endpoints, {len(tools)} tools')
    return tools


def write_available_actions_md(tools: list[dict], output_dir: Path) -> None:
    """Emit AVAILABLE_ACTIONS.md — human-readable list of valid resource:action
    tokens for DISALLOWED_ACTIONS. One row per tool; excludes pairs where every
    endpoint is deprecated (same rule as _build_tools_index)."""
    lines = [
        '# Available actions',
        '',
        'Generated from the OpenAPI spec by `split_openapi_by_endpoint.py`. '
        'Each row is a valid `resource:action` token you can use in `DISALLOWED_ACTIONS`.',
        '',
        '| resource:action | tool | endpoints |',
        '|-----------------|------|-----------|',
    ]
    for t in sorted(tools, key=lambda t: (t['resource'], t['action'])):
        lines.append(f"| {t['resource']}:{t['action']} | `{t['name']}` | {t['endpoints']} |")
    (output_dir / 'AVAILABLE_ACTIONS.md').write_text('\n'.join(lines) + '\n')
    print(f'  Wrote AVAILABLE_ACTIONS.md with {len(tools)} tools')


def _clean_desc(s: str) -> str:
    """Collapse whitespace/newlines so a spec description fits on one comment-free line."""
    return ' '.join((s or '').split())

def _to_snake(name: str) -> str:
    """camelCase -> snake_case. Used for PATH param names only (they're URL
    placeholders, not wire-visible). Never apply this to query param names."""
    return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name).lower()

def sync_param_definitions(output_dir: Path, server_file: Path) -> None:
    """Append PARAM_DEFINITIONS entries for any PATH params not already keyed there.

    - "new" == the param name is not currently a key in PARAM_DEFINITIONS.
    - Only PATH params are scanned for now. Query params are intentionally left
      out; flip the `in` filter below to extend this later (same logic).
    - Append-only: existing keys are never read for content, edited, or removed.
    - New entries are tagged with a trailing `# needs audit` marker.
    """
    source = server_file.read_text()
    tree = ast.parse(source)

    # Locate the PARAM_DEFINITIONS dict literal and its existing keys via AST,
    # so we get exact line numbers and never disturb formatting/comments.
    param_defs_node = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            if any(isinstance(t, ast.Name) and t.id == 'PARAM_DEFINITIONS'
                   for t in node.targets):
                param_defs_node = node.value
                break

    if param_defs_node is None:
        print('\n  WARNING: PARAM_DEFINITIONS not found; skipping param-definition sync.')
        return

    existing_keys = {
        k.value for k in param_defs_node.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }

    # Collect undefined path params across every generated schema file.
    # name -> (type, description) from the first file that introduces it.
    discovered: dict[str, tuple[str, str]] = {}
    for schema_path in sorted(output_dir.rglob('*.json')):
        try:
            endpoint_doc = json.loads(schema_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for p in endpoint_doc.get('parameters', []):
            kind = p.get('in')
            if kind not in ('path', 'query'):
                continue
            raw = p.get('name')
            if not raw:
                continue
            # Path params are URL placeholders -> normalize to snake_case to match
            # the convention and collapse camelCase twins (connectionId -> connection_id).
            # Query params are the literal wire format -> keep them VERBATIM, because
            # ?groupId= and ?group_id= are different requests.
            name = _to_snake(raw) if kind == 'path' else raw
            if name in existing_keys or name in discovered:
                continue
            ptype = (p.get('schema') or {}).get('type', 'string')
            pdesc = _clean_desc(p.get('description', '')) or name
            discovered[name] = (ptype, pdesc)

    if not discovered:
        print('\nPARAM_DEFINITIONS already covers every path param — nothing to add.')
        return

    # Build the new source lines.
    new_lines = []
    for name in discovered:                       # insertion order = discovery order
        ptype, pdesc = discovered[name]
        entry = {"type": ptype, "description": pdesc}
        new_lines.append(f'    {json.dumps(name)}: {json.dumps(entry)},  # needs audit')

    # Insert just before the dict's closing brace line (end_lineno is 1-indexed).
    lines = source.splitlines()
    insert_idx = param_defs_node.end_lineno - 1   # 0-indexed line of the closing "}"
    lines[insert_idx:insert_idx] = new_lines
    server_file.write_text('\n'.join(lines) + '\n')

    print(f'\nAdded {len(discovered)} param definition(s) to PARAM_DEFINITIONS (marked # needs audit):')
    for name in discovered:
        print(f'  + {name}')

def sync_tool_descriptions(output_dir: Path, server_file: Path) -> None:
    """Update descriptions of all TOOLS entries in server.py (active and commented) to match schema files."""
    lines = server_file.read_text().splitlines()
    changed = 0

    # Matches both active ("        ") and commented ("    #     ") schema_file lines
    sf_re = re.compile(r'^(        |    #     )"schema_file":\s+"([^"]+)"')
    desc_re = re.compile(r'^((?:        |    #     )"description":\s+")(.+)(",\s*)$')

    for i, line in enumerate(lines):
        sf_match = sf_re.match(line)
        if not sf_match:
            continue
        schema_path = Path(__file__).parent / sf_match.group(2)
        if not schema_path.exists():
            continue
        try:
            doc = json.loads(schema_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        new_desc = doc.get('description', '')
        if not new_desc:
            continue
        new_desc_escaped = ' '.join(new_desc.split()).replace('"', '\\"')

        # Find the description line just before schema_file (within 4 lines)
        for j in range(i - 1, max(i - 5, -1), -1):
            dm = desc_re.match(lines[j])
            if dm:
                if dm.group(2) != new_desc_escaped:
                    lines[j] = f'{dm.group(1)}{new_desc_escaped}{dm.group(3)}'
                    changed += 1
                break

    if changed:
        server_file.write_text('\n'.join(lines) + '\n')
        print(f'\nSynced {changed} tool description(s) in server.py.')
    else:
        print('\nTool descriptions in server.py are already up to date.')


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
    with open(input_file, encoding="utf-8") as f:
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

                if operation_id in EXCLUDED_ENDPOINTS:
                    print(f'  Excluded: {operation_id} (in EXCLUDED_ENDPOINTS)')
                    continue

                endpoint_doc = extract_endpoint_schema(resource_openapi, path, method)

                output_file = resource_output_dir / f'{operation_id}.json'
                output_json = json.dumps(endpoint_doc, indent=2, ensure_ascii=False)
                with open(output_file, 'w', encoding="utf-8") as f:
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

    # Emit the flat manifest (one row per endpoint) for the Step 4 router
    print('\nWriting endpoints manifest...')
    tools = write_manifest(all_mappings, output_dir)
    write_available_actions_md(tools, output_dir)

    total_endpoints = sum(len(m) for m in all_mappings.values())
    print(f'\nDone! Split into {total_endpoints} endpoint files across {len(all_mappings)} resources.')
    print(f'Total output: {total_new_lines} lines')

    # Emit per-service config schemas
    print('\nWriting per-service config schemas...')
    write_service_configs(openapi_doc, output_dir)

    # Keep server.py's PARAM_DEFINITIONS and existing tool descriptions in sync
    server_file = Path(__file__).parent / 'server.py'
    if server_file.exists():
        sync_param_definitions(output_dir, server_file)
        sync_tool_descriptions(output_dir, server_file)

    return 0


if __name__ == '__main__':
    
    exit(main())
