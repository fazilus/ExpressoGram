import re
import os
import sys
import json

# Paths - script directory is base for both input and output
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JAVA_FILE = os.path.join(SCRIPT_DIR, "TdApi.java")
KOTLIN_FILE = os.path.join(SCRIPT_DIR, "TdApi.kt")


def main():
    if not os.path.exists(JAVA_FILE):
        print(f"Error: {JAVA_FILE} not found!")
        return

    print(f"Reading {JAVA_FILE}...")
    try:
        with open(JAVA_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    print("Parsing Java classes...")

    abstract_classes = {}   # name -> kdoc (str or None)
    classes = []
    git_commit_hash = "unknown"

    re_abstract = re.compile(r'abstract\s+(?:.*\s+)?class\s+(\w+)')
    re_class = re.compile(r'^\s*(?:public\s+)?(?:static\s+)?class\s+(\w+)(?: extends ([\w<>, ]+?))?\s*\{')
    re_class_no_brace = re.compile(r'^\s*(?:public\s+)?(?:static\s+)?class\s+(\w+)')
    re_git_hash = re.compile(r'GIT_COMMIT_HASH\s*=\s*"(\w+)"')
    # Matches:  [optional @Annotations] public [optional @Annotation] Type name [= initializer];
    re_field = re.compile(
        r'^(?:@\w+\s+)*public\s+(?:@\w+\s+)*([\w.<>\[\]]+)\s+(\w+)\s*(?:=\s*[^;]+)?;'
    )
    # Heuristic: looks like a field declaration but did not match re_field above —
    # used only to flag possible silent parser regressions, never to extract data.
    re_field_suspect = re.compile(
        r'^(?:@\w+\s+)*public\s+(?!static\s+final\s+int\s+CONSTRUCTOR)(?!(?:abstract\s+)?(?:static\s+)?class\s)(?!(?:abstract\s+)?(?:static\s+)?interface\s)'
    )
    re_constructor_id = re.compile(r'CONSTRUCTOR\s*=\s*(-?\d+)\s*;')

    warnings = []

    def collect_javadoc(idx):
        """
        Looks backward from line idx and collects the Javadoc block /** ... */,
        ignoring empty lines between the comment and the declaration.
        Returns (kdoc_string | None, new_start_idx).
        new_start_idx is the index of the first line of the comment (or idx if no comment exists).
        """
        # Skip empty lines backward
        j = idx - 1
        while j >= 0 and lines[j].strip() == '':
            j -= 1
        if j < 0:
            return None, idx
        if lines[j].strip() == '*/':
            end_j = j
            while j >= 0 and '/**' not in lines[j]:
                j -= 1
            if j < 0:
                return None, idx
            comment_lines = lines[j:end_j + 1]
            # Convert to KDoc (format is already compatible)
            kdoc = ''.join(comment_lines)
            return kdoc, j
        return None, idx

    def skip_block(start_line_idx):
        """
        Skips a {} block starting at start_line_idx.
        Returns the index of the line with the closing '}'.
        """
        depth = 0
        for k in range(start_line_idx, len(lines)):
            depth += lines[k].count('{') - lines[k].count('}')
            if depth <= 0 and k >= start_line_idx:
                # Ensure that at least one '{' was encountered
                if any('{' in lines[m] for m in range(start_line_idx, k + 1)):
                    return k
        return len(lines) - 1

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines and comments (do NOT delete — they are needed for collect_javadoc)
        if not line:
            i += 1
            continue

        # Skip comment lines during main parsing
        if line.startswith("//") or line.startswith("/*") or line.startswith("*"):
            i += 1
            continue

        # GIT_COMMIT_HASH
        if "GIT_COMMIT_HASH" in line:
            m_hash = re_git_hash.search(line)
            if m_hash:
                git_commit_hash = m_hash.group(1)

        # Abstract classes
        if "abstract" in line and "class" in line:
            m_abs = re_abstract.search(line)
            if m_abs:
                name = m_abs.group(1)
                if name not in ('Object', 'Function', 'TdApi'):
                    kdoc, _ = collect_javadoc(i)
                    abstract_classes[name] = kdoc
                i = skip_block(i) + 1
                continue

        # Concrete classes
        if "class " in line and "abstract" not in line and "interface" not in line:
            m_class = re_class.search(line)
            if not m_class and re_class_no_brace.search(line):
                m_no_brace = re_class_no_brace.search(line)
                warnings.append(
                    f"line {i + 1}: class '{m_no_brace.group(1)}' declaration without "
                    f"'{{' on the same line — possibly skipped entirely: {line!r}"
                )
            if m_class:
                name = m_class.group(1)
                parent = m_class.group(2).strip() if m_class.group(2) else None

                if name == 'TdApi':
                    i += 1
                    continue

                kdoc, _ = collect_javadoc(i)

                fields = []
                field_docs = []
                constructor_id = None

                # Parse class body
                brace_depth = 0
                j = i
                while j < len(lines):
                    subline = lines[j].strip()
                    brace_depth += lines[j].count('{') - lines[j].count('}')

                    # Parse fields only at depth 1 (direct class members)
                    if brace_depth == 1:
                        m_field = re_field.match(subline)
                        if m_field:
                            f_type = m_field.group(1)
                            f_name = m_field.group(2)
                            # Collect field Javadoc
                            fdoc, _ = collect_javadoc(j)
                            fields.append((f_name, f_type))
                            field_docs.append(fdoc)
                        elif re_field_suspect.match(subline) and 'CONSTRUCTOR' not in subline:
                            warnings.append(
                                f"line {j + 1}: class '{name}' — line looks like a field "
                                f"but did not match the field pattern, possibly dropped: {subline!r}"
                            )

                        m_id = re_constructor_id.search(subline)
                        if m_id:
                            constructor_id = m_id.group(1)

                    if brace_depth <= 0 and j > i:
                        break
                    j += 1

                classes.append({
                    'name': name,
                    'parent': parent,
                    'fields': fields,
                    'field_docs': field_docs,
                    'id': constructor_id,
                    'kdoc': kdoc,
                })

                i = j + 1
                continue

        i += 1

    if git_commit_hash == "unknown":
        warnings.append(
            "GIT_COMMIT_HASH was not found in TdApi.java — the header format may have "
            "changed; TdApi.kt will report GIT_COMMIT_HASH = \"unknown\"."
        )

    total_fields = sum(len(c['fields']) for c in classes)
    print(f"Found {len(classes)} classes, {len(abstract_classes)} abstract classes, {total_fields} fields.")

    if warnings:
        print(f"\n=== {len(warnings)} parsing warning(s) ===")
        for w in warnings:
            print(f"::warning::{w}")
    else:
        print("No parsing warnings.")

    # ── Type mapping ──────────────────────────────────────────────────────────

    def map_type(java_type: str) -> str:
        # Already nullable — do not add another '?'
        if java_type.endswith('?'):
            return java_type

        primitives = {
            'int': 'Int', 'long': 'Long', 'double': 'Double',
            'boolean': 'Boolean', 'String': 'String', 'byte': 'Byte',
        }
        if java_type in primitives:
            return primitives[java_type]

        primitive_arrays = {
            'int[]': 'IntArray', 'long[]': 'LongArray',
            'double[]': 'DoubleArray', 'byte[]': 'ByteArray',
        }
        if java_type in primitive_arrays:
            return primitive_arrays[java_type]

        if java_type.endswith('[]'):
            inner = map_type(java_type[:-2])
            return f'Array<{inner}>'

        # Generic type (e.g., Function<MessageSendingState>)
        # Keep as is, add nullable
        return f'{java_type}?'

    def default_val(java_type: str) -> str:
        primitives = {
            'int': '0', 'long': '0L', 'double': '0.0',
            'boolean': 'false', 'String': '""', 'byte': '0',
        }
        if java_type in primitives:
            return primitives[java_type]

        primitive_arrays = {
            'int[]': 'IntArray(0)', 'long[]': 'LongArray(0)',
            'double[]': 'DoubleArray(0)', 'byte[]': 'ByteArray(0)',
        }
        if java_type in primitive_arrays:
            return primitive_arrays[java_type]

        if java_type.endswith('[]'):
            return 'emptyArray()'

        return 'null'

    # ── Write KT file ────────────────────────────────────────────────────────

    def write_kdoc(f, kdoc: str | None, indent: str):
        """Writes a KDoc block with the specified indent."""
        if not kdoc:
            return
        for doc_line in kdoc.splitlines(keepends=True):
            stripped = doc_line.strip()
            if stripped:
                f.write(f"{indent}{stripped}\n")
            else:
                f.write("\n")

    with open(KOTLIN_FILE, 'w', encoding='utf-8') as f:
        f.write("package org.drinkless.tdlib\n\n")
        f.write("/**\n * Auto-generated TdApi.kt from TdApi.java\n */\n")
        f.write("@Suppress(\"unused\", \"MemberVisibilityCanBePrivate\", \"NAME_SHADOWING\")\n")
        f.write("class TdApi {\n")
        f.write("    companion object {\n")
        f.write("        @kotlin.jvm.JvmField\n")
        f.write(f"        val GIT_COMMIT_HASH: String = \"{git_commit_hash}\"\n")
        f.write("    }\n\n")

        f.write("    abstract class Object {\n")
        f.write("        abstract fun getConstructor(): Int\n")
        f.write("        external override fun toString(): String\n")
        f.write("    }\n\n")

        f.write("    abstract class Function<T : Object> : Object()\n\n")

        # Abstract classes
        for abs_name in sorted(abstract_classes.keys()):
            kdoc = abstract_classes[abs_name]
            write_kdoc(f, kdoc, "    ")
            f.write(f"    abstract class {abs_name} : Object()\n")

        f.write("\n")

        # Concrete classes
        for cls in classes:
            name = cls['name']
            parent = cls['parent']
            fields = cls['fields']
            field_docs = cls['field_docs']
            cid = cls['id']
            kdoc = cls['kdoc']

            # Parent class
            if not parent or parent == 'Object':
                parent_str = 'Object()'
            elif parent.startswith('Function'):
                parent_str = f'{parent}()'
            else:
                parent_str = f'{parent}()'

            # getConstructor + companion CONSTRUCTOR
            if cid:
                constructor_impl = (
                    f"override fun getConstructor(): Int = {cid}; "
                    f"companion object {{ const val CONSTRUCTOR: Int = {cid} }}"
                )
            else:
                constructor_impl = "override fun getConstructor(): Int = 0"

            write_kdoc(f, kdoc, "    ")

            if not fields:
                f.write(f"    class {name} : {parent_str} {{ {constructor_impl} }}\n")
            else:
                f.write(f"    class {name}(\n")
                for (fname, ftype), fdoc in zip(fields, field_docs):
                    ktype = map_type(ftype)
                    kval = default_val(ftype)
                    write_kdoc(f, fdoc, "        ")
                    f.write(f"        var {fname}: {ktype} = {kval},\n")
                f.write(f"    ) : {parent_str} {{ {constructor_impl} }}\n")

        f.write("}\n")

    print(f"Done! Written to {KOTLIN_FILE}")

    # Stats for CI-side regression detection (compare against previous run's stats file).
    stats = {
        "git_commit_hash": git_commit_hash,
        "class_count": len(classes),
        "abstract_class_count": len(abstract_classes),
        "field_count": total_fields,
        "warning_count": len(warnings),
    }
    stats_path = os.path.join(SCRIPT_DIR, "TdApi.conversion-stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    print(f"Stats written to {stats_path}: {stats}")

    # Fail loudly in CI if requested and something looked off, instead of silently
    # shipping a possibly-incomplete TdApi.kt.
    if os.environ.get("CONVERTER_STRICT") == "1" and warnings:
        print(f"\nCONVERTER_STRICT=1 and {len(warnings)} warning(s) were emitted — failing.")
        sys.exit(1)


if __name__ == "__main__":
    main()