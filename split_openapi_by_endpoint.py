#!/usr/bin/env python3
"""Split OpenAPI schema files into one file per endpoint.

Takes the resource-level OpenAPI files (e.g., connections.json) and splits them
into individual endpoint files organized as:
    open-api-definitions/resource-name/operation_id.json

Usage:
    python split_openapi_by_endpoint.py
"""

import json
import re
from pathlib import Path


def find_refs(obj: any, refs: set) -> None:
    """Recursively find all $ref values in an object."""
    if isinstance(obj, dict):
        if '$ref' in obj:
            refs.add(obj['$ref'])
        for value in obj.values():
            find_refs(value, refs)
    elif isinstance(obj, list):
        for item in obj:
            find_refs(item, refs)


def resolve_component_refs(openapi_doc: dict, refs: set) -> dict:
    """Resolve $ref paths to actual component schemas, recursively including dependencies."""
    components = openapi_doc.get('components', {})
    resolved = {}
    processed_refs = set()

    def resolve_ref(ref: str):
        if ref in processed_refs:
            return
        processed_refs.add(ref)

        # Parse ref like "#/components/schemas/ConnectionResponse"
        if not ref.startswith('#/components/'):
            return

        parts = ref[len('#/components/'):].split('/')
        if len(parts) != 2:
            return

        component_type, component_name = parts
        if component_type not in components:
            return
        if component_name not in components[component_type]:
            return

        # Add to resolved
        if component_type not in resolved:
            resolved[component_type] = {}
        resolved[component_type][component_name] = components[component_type][component_name]

        # Find nested refs
        nested_refs = set()
        find_refs(components[component_type][component_name], nested_refs)
        for nested_ref in nested_refs:
            resolve_ref(nested_ref)

    for ref in refs:
        resolve_ref(ref)

    return resolved


def extract_endpoint_schema(openapi_doc: dict, path: str, method: str) -> dict:
    """Extract a single endpoint's schema into a standalone document."""
    path_item = openapi_doc['paths'][path]
    operation = path_item[method]

    # Build a minimal OpenAPI doc for this endpoint
    endpoint_doc = {
        'openapi': openapi_doc.get('openapi', '3.0.1'),
        'info': {
            'title': operation.get('summary', f'{method.upper()} {path}'),
            'description': operation.get('description', ''),
        },
        'path': path,
        'method': method.upper(),
        'operation': operation,
    }

    # Only include referenced components (not all)
    refs = set()
    find_refs(operation, refs)

    if refs:
        resolved_components = resolve_component_refs(openapi_doc, refs)
        if resolved_components:
            endpoint_doc['components'] = resolved_components

    return endpoint_doc


def split_resource_file(resource_file: Path, output_dir: Path) -> dict:
    """Split a resource's OpenAPI file into individual endpoint files.

    Returns a mapping of operation_ids to their file paths.
    """
    with open(resource_file) as f:
        openapi_doc = json.load(f)

    resource_name = resource_file.stem  # e.g., 'connections' from 'connections.json'
    resource_output_dir = output_dir / resource_name
    resource_output_dir.mkdir(parents=True, exist_ok=True)

    endpoint_mapping = {}

    for path, path_item in openapi_doc.get('paths', {}).items():
        for method in ['get', 'post', 'put', 'patch', 'delete']:
            if method not in path_item:
                continue

            operation = path_item[method]
            operation_id = operation.get('operationId')

            if not operation_id:
                print(f'  WARNING: No operationId for {method.upper()} {path}, skipping')
                continue

            endpoint_doc = extract_endpoint_schema(openapi_doc, path, method)

            output_file = resource_output_dir / f'{operation_id}.json'
            with open(output_file, 'w') as f:
                json.dump(endpoint_doc, f, indent=2)

            endpoint_mapping[operation_id] = {
                'file': str(output_file.relative_to(output_dir.parent)),
                'path': path,
                'method': method.upper(),
                'summary': operation.get('summary', ''),
            }

            print(f'  Created: {operation_id}.json')

    return endpoint_mapping


def get_resource_from_path(path: str) -> str:
    """Extract the resource name from an API path.

    Examples:
        /v1/connections -> connections
        /v1/connections/{connectionId} -> connections
        /v1/groups/{groupId}/connections -> groups
    """
    # Remove version prefix
    path = re.sub(r'^/v\d+/', '', path)
    parts = [p for p in path.split('/') if p and not p.startswith('{')]
    return parts[0] if parts else 'other'


def main():
    """Main entry point.

    Usage:
        python split_openapi_by_endpoint.py <input_file> <output_dir>

    Example:
        python split_openapi_by_endpoint.py fivetran-open-api-definition.json open-api-definitions
    """
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

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load the full OpenAPI spec
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

    for resource_name, resource_doc in sorted(resources.items()):
        print(f'Processing {resource_name}...')

        # Create a minimal OpenAPI doc for this resource
        resource_openapi = {
            'openapi': openapi_doc.get('openapi', '3.0.1'),
            'info': openapi_doc.get('info', {}),
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
                with open(output_file, 'w') as f:
                    json.dump(endpoint_doc, f, indent=2)

                endpoint_mapping[operation_id] = {
                    'file': str(output_file.relative_to(output_dir)),
                    'path': path,
                    'method': method.upper(),
                    'summary': operation.get('summary', ''),
                }

                print(f'  Created: {operation_id}.json')

        all_mappings[resource_name] = endpoint_mapping
        print()

    # Write an index file for reference
    index_file = output_dir / 'endpoint-index.json'
    with open(index_file, 'w') as f:
        json.dump(all_mappings, f, indent=2)
    print(f'Created endpoint index: {index_file}')

    # Summary
    total_endpoints = sum(len(m) for m in all_mappings.values())
    print(f'\nDone! Split into {total_endpoints} endpoint files across {len(all_mappings)} resources.')

    return 0


if __name__ == '__main__':
    exit(main())
