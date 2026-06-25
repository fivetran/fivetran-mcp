# Refresh OpenAPI Tool Definitions

Refreshes all tool entries in `server.py` from the latest Fivetran OpenAPI spec. Deletes every entry (active and commented), regenerates them fresh from the spec via the split script, then restores which tools were active before. Run this whenever the OpenAPI spec changes and you want updated tool definitions.

## Steps

### 1. Collect active schema_files

Read `server.py`. Collect the value of every `"schema_file"` line that is **not** commented out. These are the active tools that must be restored after regeneration.

```python
import re
content = open('server.py').read()
active_schema_files = set(re.findall(r'^        "schema_file": "([^"]+)"', content, re.MULTILINE))
```

Save this set — you need it in step 4.

---

### 2. Clean all tool entries from server.py

Write and run `/tmp/ft_clean_tools.py`. This removes every tool entry (active and commented) from the `TOOLS` dict while preserving section headers, blank lines between sections, and the dict open/close braces.

```python
import re

with open('server.py') as f:
    lines = f.read().splitlines()

tools_start = next(i for i, l in enumerate(lines) if l.startswith('TOOLS = {'))
tools_end = next(i for i in range(tools_start + 1, len(lines)) if lines[i].rstrip() == '}')

result = []
i = 0
while i < len(lines):
    if i <= tools_start or i >= tools_end:
        result.append(lines[i])
        i += 1
        continue

    line = lines[i]

    # Active tool entry: `    "name": {`
    if re.match(r'^    "[^"]+": \{', line):
        depth = 0
        while i < tools_end:
            depth += lines[i].count('{') - lines[i].count('}')
            i += 1
            if depth == 0:
                break
        if i < tools_end and not lines[i].strip():
            i += 1
        continue

    # Commented tool entry: `    # "name": {`
    if re.match(r'^    # "[^"]+": \{', line):
        i += 1
        while i < tools_end:
            cur = lines[i]
            i += 1
            if re.match(r'^    # \},\s*$', cur):
                break
        if i < tools_end and not lines[i].strip():
            i += 1
        continue

    result.append(line)
    i += 1

with open('server.py', 'w') as f:
    f.write('\n'.join(result) + '\n')
print('Cleaned server.py')
```

Run it:
```
python3 /tmp/ft_clean_tools.py
```

---

### 3. Run the split script

From the project root:
```
python split_openapi_by_endpoint.py fivetran-open-api-definition.json open-api-definitions
```

This wipes and rebuilds `open-api-definitions/`, then injects every endpoint back into `server.py` as a commented-out stub with fresh data from the spec.

---

### 4. Restore active tools

Write and run `/tmp/ft_restore_tools.py`, passing the `active_schema_files` from step 1 as a JSON array argument. This finds each previously-active tool by its schema_file path and uncomments it.

```python
import re, json, sys

active_schema_files = json.loads(sys.argv[1])

with open('server.py') as f:
    lines = f.read().splitlines()

restored = []
orphaned = []

for schema_file in active_schema_files:
    # Find the commented line referencing this schema_file
    sf_idx = next(
        (i for i, l in enumerate(lines)
         if l.startswith('    # ') and f'"schema_file": "{schema_file}"' in l),
        None
    )
    if sf_idx is None:
        orphaned.append(schema_file)
        continue

    # Scan up to find entry start: `    # "name": {`
    start_idx = sf_idx
    while start_idx > 0 and not re.match(r'^    # "[^"]+": \{', lines[start_idx]):
        start_idx -= 1

    # Scan down to find entry end: `    # },`
    end_idx = sf_idx
    while end_idx < len(lines) and not re.match(r'^    # \},\s*$', lines[end_idx]):
        end_idx += 1

    # Extract tool name for reporting
    name_match = re.match(r'^    # "([^"]+)": \{', lines[start_idx])
    tool_name = name_match.group(1) if name_match else schema_file

    # Uncomment: remove `# ` at positions 4-5 of each line
    for idx in range(start_idx, end_idx + 1):
        if len(lines[idx]) > 5 and lines[idx][4:6] == '# ':
            lines[idx] = lines[idx][:4] + lines[idx][6:]

    restored.append(tool_name)

with open('server.py', 'w') as f:
    f.write('\n'.join(lines) + '\n')

print(f'Restored {len(restored)} active tools:')
for name in sorted(restored):
    print(f'  + {name}')
if orphaned:
    print(f'\nSkipped {len(orphaned)} (not found in new spec — endpoint likely removed):')
    for sf in orphaned:
        print(f'  - {sf}')
```

Run it, passing the active schema_files as JSON:
```
python3 /tmp/ft_restore_tools.py '<JSON_ARRAY_OF_ACTIVE_SCHEMA_FILES>'
```

---

### 5. Verify and clean up

Confirm the file is valid Python:
```
python3 -c "import ast; ast.parse(open('server.py').read()); print('server.py: valid Python')"
```

Delete temp files:
```
rm /tmp/ft_clean_tools.py /tmp/ft_restore_tools.py
```

Report a summary: how many tools were refreshed total, how many were restored as active, and any orphaned actives.
