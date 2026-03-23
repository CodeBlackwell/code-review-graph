"""Tree-sitter based multi-language code parser.

Extracts structural nodes (classes, functions, imports, types) and edges
(calls, inheritance, contains) from source files.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import tree_sitter_language_pack as tslp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models for extracted entities
# ---------------------------------------------------------------------------


@dataclass
class NodeInfo:
    kind: str  # File, Class, Function, Type, Test
    name: str
    file_path: str
    line_start: int
    line_end: int
    language: str = ""
    parent_name: Optional[str] = None  # enclosing class/module
    params: Optional[str] = None
    return_type: Optional[str] = None
    modifiers: Optional[str] = None
    is_test: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class EdgeInfo:
    kind: str  # CALLS, IMPORTS_FROM, INHERITS, ..., DEPENDS_ON, OVERRIDES
    source: str  # qualified name or path
    target: str  # qualified name or path
    file_path: str
    line: int = 0
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Language extension mapping
# ---------------------------------------------------------------------------

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".rb": "ruby",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".php": "php",
    ".sol": "solidity",
    ".vue": "vue",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
}

# Tree-sitter node type mappings per language
# Maps (language) -> dict of semantic role -> list of TS node types
_CLASS_TYPES: dict[str, list[str]] = {
    "python": ["class_definition"],
    "javascript": ["class_declaration", "class"],
    "typescript": ["class_declaration", "class"],
    "tsx": ["class_declaration", "class"],
    "go": ["type_declaration"],
    "rust": ["struct_item", "enum_item", "impl_item"],
    "java": ["class_declaration", "interface_declaration", "enum_declaration"],
    "c": ["struct_specifier", "type_definition"],
    "cpp": ["class_specifier", "struct_specifier"],
    "csharp": [
        "class_declaration", "interface_declaration",
        "enum_declaration", "struct_declaration",
    ],
    "ruby": ["class", "module"],
    "kotlin": ["class_declaration", "object_declaration"],
    "swift": ["class_declaration", "struct_declaration", "protocol_declaration"],
    "php": ["class_declaration", "interface_declaration"],
    "solidity": [
        "contract_declaration", "interface_declaration", "library_declaration",
        "struct_declaration", "enum_declaration", "error_declaration",
        "user_defined_type_definition",
    ],
}

_FUNCTION_TYPES: dict[str, list[str]] = {
    "python": ["function_definition"],
    "javascript": ["function_declaration", "method_definition", "arrow_function"],
    "typescript": ["function_declaration", "method_definition", "arrow_function"],
    "tsx": ["function_declaration", "method_definition", "arrow_function"],
    "go": ["function_declaration", "method_declaration"],
    "rust": ["function_item"],
    "java": ["method_declaration", "constructor_declaration"],
    "c": ["function_definition"],
    "cpp": ["function_definition"],
    "csharp": ["method_declaration", "constructor_declaration"],
    "ruby": ["method", "singleton_method"],
    "kotlin": ["function_declaration"],
    "swift": ["function_declaration"],
    "php": ["function_definition", "method_declaration"],
    # Solidity: events and modifiers use kind="Function" because the graph
    # schema has no dedicated kind for them.  State variables are also modeled
    # as Function nodes (public ones auto-generate getters) and distinguished
    # via extra["solidity_kind"].
    "solidity": [
        "function_definition", "constructor_definition", "modifier_definition",
        "event_definition", "fallback_receive_definition",
    ],
}

_IMPORT_TYPES: dict[str, list[str]] = {
    "python": ["import_statement", "import_from_statement"],
    "javascript": ["import_statement"],
    "typescript": ["import_statement"],
    "tsx": ["import_statement"],
    "go": ["import_declaration"],
    "rust": ["use_declaration"],
    "java": ["import_declaration"],
    "c": ["preproc_include"],
    "cpp": ["preproc_include"],
    "csharp": ["using_directive"],
    "ruby": ["call"],  # require/require_relative
    "kotlin": ["import_header"],
    "swift": ["import_declaration"],
    "php": ["namespace_use_declaration"],
    "solidity": ["import_directive"],
}

_CALL_TYPES: dict[str, list[str]] = {
    "python": ["call"],
    "javascript": ["call_expression", "new_expression"],
    "typescript": ["call_expression", "new_expression"],
    "tsx": ["call_expression", "new_expression"],
    "go": ["call_expression"],
    "rust": ["call_expression", "macro_invocation"],
    "java": ["method_invocation", "object_creation_expression"],
    "c": ["call_expression"],
    "cpp": ["call_expression"],
    "csharp": ["invocation_expression", "object_creation_expression"],
    "ruby": ["call", "method_call"],
    "kotlin": ["call_expression"],
    "swift": ["call_expression"],
    "php": ["function_call_expression", "member_call_expression"],
    "solidity": ["call_expression"],
}

# Patterns that indicate a test function
_TEST_PATTERNS = [
    re.compile(r"^test_"),
    re.compile(r"^Test"),
    re.compile(r"_test$"),
    re.compile(r"\.test\."),
    re.compile(r"\.spec\."),
    re.compile(r"_spec$"),
]

_TEST_FILE_PATTERNS = [
    re.compile(r"test_.*\.py$"),
    re.compile(r".*_test\.py$"),
    re.compile(r".*\.test\.[jt]sx?$"),
    re.compile(r".*\.spec\.[jt]sx?$"),
    re.compile(r".*_test\.go$"),
    re.compile(r"tests?/"),
]


def _is_test_file(path: str) -> bool:
    return any(p.search(path) for p in _TEST_FILE_PATTERNS)


def _is_test_function(name: str, file_path: str) -> bool:
    """A function is a test if its name matches test patterns or it lives
    in a test file and has a test-runner name (describe, it, test, etc.).
    """
    if any(p.search(name) for p in _TEST_PATTERNS):
        return True
    # In test files, treat common JS/TS test-runner wrappers as tests
    if _is_test_file(file_path) and name in (
        "describe", "it", "test", "beforeEach", "afterEach",
        "beforeAll", "afterAll",
    ):
        return True
    return False


def file_hash(path: Path) -> str:
    """SHA-256 hash of file contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class CodeParser:
    """Parses source files using Tree-sitter and extracts structural information."""

    _MODULE_CACHE_MAX = 15_000  # Evict cache to cap memory on huge monorepos

    def __init__(self) -> None:
        self._parsers: dict[str, object] = {}
        self._module_file_cache: dict[str, Optional[str]] = {}

    def _get_parser(self, language: str):  # type: ignore[arg-type]
        if language not in self._parsers:
            try:
                self._parsers[language] = tslp.get_parser(language)  # type: ignore[arg-type]
            except Exception:
                return None
        return self._parsers[language]

    def detect_language(self, path: Path) -> Optional[str]:
        return EXTENSION_TO_LANGUAGE.get(path.suffix.lower())

    def parse_file(self, path: Path) -> tuple[list[NodeInfo], list[EdgeInfo]]:
        """Parse a single file and return extracted nodes and edges."""
        try:
            source = path.read_bytes()
        except (OSError, PermissionError):
            return [], []
        return self.parse_bytes(path, source)

    def parse_bytes(self, path: Path, source: bytes) -> tuple[list[NodeInfo], list[EdgeInfo]]:
        """Parse pre-read bytes and return extracted nodes and edges.

        This avoids re-reading the file from disk, eliminating TOCTOU gaps
        when the caller has already read the bytes (e.g. for hashing).
        """
        language = self.detect_language(path)
        if not language:
            return [], []

        # Vue SFCs: parse with vue parser, then delegate script blocks to JS/TS
        if language == "vue":
            return self._parse_vue(path, source)

        # CSS/SCSS: dedicated parser for stylesheet languages
        if language in ("css", "scss"):
            return self._parse_css(path, source, language)

        # LESS: regex-based fallback (no tree-sitter grammar available)
        if language == "less":
            return self._parse_less(path, source)

        parser = self._get_parser(language)
        if not parser:
            return [], []

        tree = parser.parse(source)
        nodes: list[NodeInfo] = []
        edges: list[EdgeInfo] = []
        file_path_str = str(path)

        # File node
        test_file = _is_test_file(file_path_str)
        nodes.append(NodeInfo(
            kind="File",
            name=file_path_str,
            file_path=file_path_str,
            line_start=1,
            line_end=source.count(b"\n") + 1,
            language=language,
            is_test=test_file,
        ))

        # Pre-scan for import mappings and defined names
        import_map, defined_names = self._collect_file_scope(
            tree.root_node, language, source,
        )

        # Walk the tree (with JSX class collection for JSX-capable languages)
        jsx_class_collector: dict[str, dict] = {}
        self._extract_from_tree(
            tree.root_node, source, language, file_path_str, nodes, edges,
            import_map=import_map, defined_names=defined_names,
            jsx_class_collector=jsx_class_collector,
        )

        # Attach collected JSX class refs to their enclosing function nodes
        if jsx_class_collector:
            node_by_qn: dict[str, NodeInfo] = {}
            for n in nodes:
                qn = self._qualify(n.name, n.file_path, n.parent_name)
                node_by_qn[qn] = n
            for func_qn, refs in jsx_class_collector.items():
                target_node = node_by_qn.get(func_qn)
                if target_node:
                    if refs.get("classes"):
                        target_node.extra["css_classes"] = sorted(
                            set(refs["classes"]),
                        )
                    if refs.get("module_refs"):
                        target_node.extra["css_module_refs"] = [
                            {"import": imp, "property": prop}
                            for imp, prop in refs["module_refs"]
                        ]

        # Detect CSS Module imports and store on File node
        if import_map:
            css_mod_imports = {
                name: mod for name, mod in import_map.items()
                if ".module.css" in mod or ".module.scss" in mod
            }
            if css_mod_imports:
                nodes[0].extra["css_module_imports"] = css_mod_imports

        # Resolve bare call targets to qualified names using same-file definitions
        edges = self._resolve_call_targets(nodes, edges, file_path_str)

        # Generate TESTED_BY edges: when a test function calls a production
        # function, create an edge from the production function back to the test.
        if test_file:
            test_qnames = set()
            for n in nodes:
                if n.is_test:
                    qn = self._qualify(n.name, n.file_path, n.parent_name)
                    test_qnames.add(qn)
            for edge in list(edges):
                if edge.kind == "CALLS" and edge.source in test_qnames:
                    edges.append(EdgeInfo(
                        kind="TESTED_BY",
                        source=edge.target,
                        target=edge.source,
                        file_path=edge.file_path,
                        line=edge.line,
                    ))

        return nodes, edges

    def _parse_vue(
        self, path: Path, source: bytes,
    ) -> tuple[list[NodeInfo], list[EdgeInfo]]:
        """Parse a Vue SFC by extracting <script> blocks and delegating to JS/TS."""
        vue_parser = self._get_parser("vue")
        if not vue_parser:
            return [], []

        tree = vue_parser.parse(source)
        file_path_str = str(path)
        test_file = _is_test_file(file_path_str)

        all_nodes: list[NodeInfo] = [NodeInfo(
            kind="File",
            name=file_path_str,
            file_path=file_path_str,
            line_start=1,
            line_end=source.count(b"\n") + 1,
            language="vue",
            is_test=test_file,
        )]
        all_edges: list[EdgeInfo] = []

        # Find script_element blocks in the Vue AST
        for child in tree.root_node.children:
            if child.type != "script_element":
                continue

            # Detect language from lang="ts" attribute
            script_lang = "javascript"
            start_tag = None
            raw_text_node = None
            for sub in child.children:
                if sub.type == "start_tag":
                    start_tag = sub
                elif sub.type == "raw_text":
                    raw_text_node = sub

            if start_tag:
                for attr in start_tag.children:
                    if attr.type == "attribute":
                        attr_name = None
                        attr_value = None
                        for a in attr.children:
                            if a.type == "attribute_name":
                                attr_name = a.text.decode("utf-8", errors="replace")
                            elif a.type == "quoted_attribute_value":
                                for v in a.children:
                                    if v.type == "attribute_value":
                                        attr_value = v.text.decode(
                                            "utf-8", errors="replace",
                                        )
                        if attr_name == "lang" and attr_value in ("ts", "typescript"):
                            script_lang = "typescript"

            if not raw_text_node:
                continue

            script_source = raw_text_node.text
            line_offset = raw_text_node.start_point[0]  # 0-based line of raw_text start

            # Parse the script block with the appropriate JS/TS parser
            script_parser = self._get_parser(script_lang)
            if not script_parser:
                continue

            script_tree = script_parser.parse(script_source)

            # Collect imports and defined names from the script block
            import_map, defined_names = self._collect_file_scope(
                script_tree.root_node, script_lang, script_source,
            )

            nodes: list[NodeInfo] = []
            edges: list[EdgeInfo] = []
            self._extract_from_tree(
                script_tree.root_node, script_source, script_lang,
                file_path_str, nodes, edges,
                import_map=import_map, defined_names=defined_names,
            )

            # Adjust line numbers to account for position within the .vue file
            for node in nodes:
                node.line_start += line_offset
                node.line_end += line_offset
                node.language = "vue"
            for edge in edges:
                edge.line += line_offset

            all_nodes.extend(nodes)
            all_edges.extend(edges)

        # Extract <style> blocks and delegate to CSS/SCSS parser
        for child in tree.root_node.children:
            if child.type != "style_element":
                continue

            style_lang = "css"
            raw_text_node = None
            start_tag = None
            for sub in child.children:
                if sub.type == "start_tag":
                    start_tag = sub
                elif sub.type == "raw_text":
                    raw_text_node = sub

            if start_tag:
                for attr in start_tag.children:
                    if attr.type == "attribute":
                        attr_name = None
                        attr_value = None
                        for a in attr.children:
                            if a.type == "attribute_name":
                                attr_name = a.text.decode("utf-8", errors="replace")
                            elif a.type == "quoted_attribute_value":
                                for v in a.children:
                                    if v.type == "attribute_value":
                                        attr_value = v.text.decode(
                                            "utf-8", errors="replace",
                                        )
                        if attr_name == "lang" and attr_value in ("scss", "sass"):
                            style_lang = "scss"

            if not raw_text_node:
                continue

            style_source = raw_text_node.text
            style_line_offset = raw_text_node.start_point[0]

            style_nodes, style_edges = self._parse_css(
                path, style_source, style_lang, line_offset=style_line_offset,
            )

            # Remove the File node that _parse_css creates (we already have one)
            style_nodes = [n for n in style_nodes if n.kind != "File"]
            for sn in style_nodes:
                sn.language = "vue"

            all_nodes.extend(style_nodes)
            all_edges.extend(style_edges)

        # Extract static class names from <template> elements
        for child in tree.root_node.children:
            if child.type == "template_element":
                template_classes = self._extract_vue_template_classes(child)
                if template_classes:
                    all_nodes[0].extra["vue_template_classes"] = sorted(
                        set(template_classes),
                    )
                break  # Only one <template> per SFC

        # Generate TESTED_BY edges
        if test_file:
            test_qnames = set()
            for n in all_nodes:
                if n.is_test:
                    qn = self._qualify(n.name, n.file_path, n.parent_name)
                    test_qnames.add(qn)
            for edge in list(all_edges):
                if edge.kind == "CALLS" and edge.source in test_qnames:
                    all_edges.append(EdgeInfo(
                        kind="TESTED_BY",
                        source=edge.target,
                        target=edge.source,
                        file_path=edge.file_path,
                        line=edge.line,
                    ))

        return all_nodes, all_edges

    # -------------------------------------------------------------------
    # CSS / SCSS parsing
    # -------------------------------------------------------------------

    @dataclass
    class _SelectorRecord:
        """Ephemeral record for CSS override detection within a single file."""

        selector_text: str
        qualified_name: str
        specificity: tuple[int, int, int]
        properties: dict[str, int]          # property_name -> line
        has_important: dict[str, bool]      # property_name -> has !important
        line_start: int
        line_end: int
        source_order: int

    def _parse_css(
        self,
        path: Path,
        source: bytes,
        language: str = "css",
        line_offset: int = 0,
    ) -> tuple[list[NodeInfo], list[EdgeInfo]]:
        """Parse a CSS/SCSS file and extract selectors, custom properties,
        mixins, variables, @import, var() usage, and override relationships."""
        css_parser = self._get_parser(language)
        if not css_parser:
            return [], []

        tree = css_parser.parse(source)
        file_path_str = str(path)

        nodes: list[NodeInfo] = [NodeInfo(
            kind="File",
            name=file_path_str,
            file_path=file_path_str,
            line_start=1 + line_offset,
            line_end=source.count(b"\n") + 1 + line_offset,
            language=language,
        )]
        edges: list[EdgeInfo] = []
        selector_records: list[CodeParser._SelectorRecord] = []

        self._walk_css(
            tree.root_node, source, language, file_path_str,
            nodes, edges, selector_records,
            enclosing_context=None, line_offset=line_offset,
        )

        # Override detection (post-processing after all selectors collected)
        override_edges = self._detect_css_overrides(selector_records, file_path_str)
        edges.extend(override_edges)

        return nodes, edges

    def _walk_css(
        self,
        node,
        source: bytes,
        language: str,
        file_path: str,
        nodes: list[NodeInfo],
        edges: list[EdgeInfo],
        selector_records: list[_SelectorRecord],
        enclosing_context: Optional[str] = None,
        line_offset: int = 0,
        _depth: int = 0,
    ) -> None:
        """Recursively walk CSS/SCSS AST extracting nodes and edges."""
        if _depth > self._MAX_AST_DEPTH:
            return

        for child in node.children:
            node_type = child.type

            # --- @import / @use ---
            if node_type in ("import_statement", "use_statement"):
                target = self._extract_css_import(child)
                if target:
                    edges.append(EdgeInfo(
                        kind="IMPORTS_FROM",
                        source=file_path,
                        target=target,
                        file_path=file_path,
                        line=child.start_point[0] + 1 + line_offset,
                    ))
                continue

            # --- rule_set (selector + block) ---
            if node_type == "rule_set":
                self._handle_css_ruleset(
                    child, source, language, file_path,
                    nodes, edges, selector_records,
                    enclosing_context, line_offset, _depth,
                )
                continue

            # --- @media ---
            if node_type == "media_statement":
                media_text = self._get_css_media_text(child)
                media_name = f"@media({media_text})"
                qualified = self._qualify(media_name, file_path, None)

                nodes.append(NodeInfo(
                    kind="Class",
                    name=media_name,
                    file_path=file_path,
                    line_start=child.start_point[0] + 1 + line_offset,
                    line_end=child.end_point[0] + 1 + line_offset,
                    language=language,
                    extra={"css_kind": "media_query"},
                ))
                edges.append(EdgeInfo(
                    kind="CONTAINS",
                    source=file_path,
                    target=qualified,
                    file_path=file_path,
                    line=child.start_point[0] + 1 + line_offset,
                ))

                for sub in child.children:
                    if sub.type == "block":
                        self._walk_css(
                            sub, source, language, file_path,
                            nodes, edges, selector_records,
                            enclosing_context=media_name,
                            line_offset=line_offset,
                            _depth=_depth + 1,
                        )
                continue

            # --- @keyframes ---
            if node_type == "keyframes_statement":
                kf_name = None
                for sub in child.children:
                    if sub.type == "keyframes_name":
                        kf_name = sub.text.decode("utf-8", errors="replace")
                if kf_name:
                    name = f"@keyframes({kf_name})"
                    nodes.append(NodeInfo(
                        kind="Class",
                        name=name,
                        file_path=file_path,
                        line_start=child.start_point[0] + 1 + line_offset,
                        line_end=child.end_point[0] + 1 + line_offset,
                        language=language,
                        extra={"css_kind": "keyframes"},
                    ))
                    edges.append(EdgeInfo(
                        kind="CONTAINS",
                        source=file_path,
                        target=self._qualify(name, file_path, None),
                        file_path=file_path,
                        line=child.start_point[0] + 1 + line_offset,
                    ))
                continue

            # --- SCSS: top-level $variable declarations ---
            if language == "scss" and node_type == "declaration":
                prop_name = self._get_css_property_name(child)
                if prop_name and prop_name.startswith("$"):
                    nodes.append(NodeInfo(
                        kind="Function",
                        name=prop_name,
                        file_path=file_path,
                        line_start=child.start_point[0] + 1 + line_offset,
                        line_end=child.end_point[0] + 1 + line_offset,
                        language=language,
                        extra={"css_kind": "scss_variable"},
                    ))
                    container = (
                        self._qualify(enclosing_context, file_path, None)
                        if enclosing_context
                        else file_path
                    )
                    edges.append(EdgeInfo(
                        kind="CONTAINS",
                        source=container,
                        target=self._qualify(prop_name, file_path, None),
                        file_path=file_path,
                        line=child.start_point[0] + 1 + line_offset,
                    ))
                continue

            # --- SCSS: @mixin ---
            if language == "scss" and node_type == "mixin_statement":
                mixin_name = None
                for sub in child.children:
                    if sub.type == "identifier":
                        mixin_name = sub.text.decode("utf-8", errors="replace")
                        break
                if mixin_name:
                    nodes.append(NodeInfo(
                        kind="Function",
                        name=mixin_name,
                        file_path=file_path,
                        line_start=child.start_point[0] + 1 + line_offset,
                        line_end=child.end_point[0] + 1 + line_offset,
                        language=language,
                        extra={"css_kind": "mixin"},
                    ))
                    edges.append(EdgeInfo(
                        kind="CONTAINS",
                        source=file_path,
                        target=self._qualify(mixin_name, file_path, None),
                        file_path=file_path,
                        line=child.start_point[0] + 1 + line_offset,
                    ))
                continue

            # --- Recurse into other nodes ---
            self._walk_css(
                child, source, language, file_path,
                nodes, edges, selector_records,
                enclosing_context=enclosing_context,
                line_offset=line_offset,
                _depth=_depth + 1,
            )

    def _handle_css_ruleset(
        self,
        node,
        source: bytes,
        language: str,
        file_path: str,
        nodes: list[NodeInfo],
        edges: list[EdgeInfo],
        selector_records: list[_SelectorRecord],
        enclosing_context: Optional[str],
        line_offset: int,
        _depth: int,
    ) -> None:
        """Process a CSS rule_set: extract selectors, properties, var() refs."""
        selectors_node = None
        block_node = None
        for sub in node.children:
            if sub.type == "selectors":
                selectors_node = sub
            elif sub.type == "block":
                block_node = sub

        if not selectors_node:
            return

        # Extract properties and var() refs from the block
        properties: dict[str, int] = {}
        has_important: dict[str, bool] = {}
        var_refs: list[tuple[str, int]] = []   # (var_name, line)
        include_refs: list[tuple[str, int]] = []  # (mixin_name, line)
        custom_prop_defs: list[tuple[str, int, int]] = []  # (name, line_start, line_end)

        if block_node:
            for decl in block_node.children:
                if decl.type == "declaration":
                    prop = self._get_css_property_name(decl)
                    if prop:
                        line = decl.start_point[0] + 1 + line_offset
                        if prop.startswith("--"):
                            # Custom property definition
                            custom_prop_defs.append((
                                prop, line, decl.end_point[0] + 1 + line_offset,
                            ))
                        else:
                            properties[prop] = line
                            has_important[prop] = self._has_important(decl)
                        # Check for var() references in value
                        for var_name in self._find_var_refs(decl):
                            var_refs.append((var_name, line))
                elif decl.type == "include_statement" and language == "scss":
                    # @include mixin_name
                    for sub in decl.children:
                        if sub.type == "identifier":
                            include_refs.append((
                                sub.text.decode("utf-8", errors="replace"),
                                decl.start_point[0] + 1 + line_offset,
                            ))
                            break

        # Split comma-separated selectors into individual nodes
        individual_selectors = self._split_css_selectors(selectors_node)

        # Create custom property nodes once (not per selector in comma groups)
        seen_custom_props: set[str] = set()
        first_qualified: Optional[str] = None

        for sel_text, sel_node in individual_selectors:
            # Resolve SCSS nesting selector (&)
            if language == "scss" and enclosing_context and "&" in sel_text:
                # For BEM: &-primary → .btn-primary (parent is .btn)
                parent_sel = enclosing_context
                if parent_sel.startswith("@"):
                    parent_sel = ""  # Don't resolve & against @media
                sel_text = sel_text.replace("&", parent_sel)

            specificity = self._compute_specificity(sel_node)
            qualified = self._qualify(sel_text, file_path, enclosing_context)
            if first_qualified is None:
                first_qualified = qualified

            nodes.append(NodeInfo(
                kind="Class",
                name=sel_text,
                file_path=file_path,
                line_start=node.start_point[0] + 1 + line_offset,
                line_end=node.end_point[0] + 1 + line_offset,
                language=language,
                parent_name=enclosing_context,
                extra={"css_kind": "selector", "specificity": list(specificity)},
            ))

            # CONTAINS edge
            container = (
                self._qualify(enclosing_context, file_path, None)
                if enclosing_context
                else file_path
            )
            edges.append(EdgeInfo(
                kind="CONTAINS",
                source=container,
                target=qualified,
                file_path=file_path,
                line=node.start_point[0] + 1 + line_offset,
            ))

            # Custom property definitions — only create once, parent to first selector
            for cp_name, cp_start, cp_end in custom_prop_defs:
                if cp_name in seen_custom_props:
                    continue
                seen_custom_props.add(cp_name)
                cp_qualified = self._qualify(cp_name, file_path, None)
                nodes.append(NodeInfo(
                    kind="Function",
                    name=cp_name,
                    file_path=file_path,
                    line_start=cp_start,
                    line_end=cp_end,
                    language=language,
                    parent_name=sel_text,
                    extra={"css_kind": "custom_property"},
                ))
                edges.append(EdgeInfo(
                    kind="CONTAINS",
                    source=first_qualified,
                    target=cp_qualified,
                    file_path=file_path,
                    line=cp_start,
                ))

            # var() references → CALLS edges
            for var_name, var_line in var_refs:
                edges.append(EdgeInfo(
                    kind="CALLS",
                    source=qualified,
                    target=var_name,
                    file_path=file_path,
                    line=var_line,
                ))

            # SCSS @include → CALLS edges
            for mixin_name, inc_line in include_refs:
                edges.append(EdgeInfo(
                    kind="CALLS",
                    source=qualified,
                    target=mixin_name,
                    file_path=file_path,
                    line=inc_line,
                ))

            # Record for override detection
            selector_records.append(CodeParser._SelectorRecord(
                selector_text=sel_text,
                qualified_name=qualified,
                specificity=specificity,
                properties=dict(properties),
                has_important=dict(has_important),
                line_start=node.start_point[0] + 1 + line_offset,
                line_end=node.end_point[0] + 1 + line_offset,
                source_order=len(selector_records),
            ))

        # Recurse into nested rule_sets (SCSS nesting / CSS nesting)
        if block_node:
            for sub in block_node.children:
                if sub.type == "rule_set":
                    # Use the first selector as enclosing context for nesting
                    ctx = individual_selectors[0][0] if individual_selectors else enclosing_context
                    self._handle_css_ruleset(
                        sub, source, language, file_path,
                        nodes, edges, selector_records,
                        enclosing_context=ctx,
                        line_offset=line_offset,
                        _depth=_depth + 1,
                    )

    def _compute_specificity(self, selector_node) -> tuple[int, int, int]:
        """Compute CSS specificity (a, b, c) from a selector AST node.

        a = ID selectors, b = class/pseudo-class/attribute, c = type/pseudo-element.
        """
        a = b = c = 0
        stack = [selector_node]
        while stack:
            n = stack.pop()
            t = n.type
            if t == "id_selector":
                a += 1
            elif t in ("class_selector", "pseudo_class_selector", "attribute_selector"):
                b += 1
            elif t == "pseudo_element_selector":
                c += 1
            elif t == "tag_name":
                # Don't count tag_name inside pseudo_element_selector (already counted)
                if not (n.parent and n.parent.type == "pseudo_element_selector"):
                    c += 1
            for child in n.children:
                stack.append(child)
        return (a, b, c)

    def _detect_css_overrides(
        self,
        records: list[_SelectorRecord],
        file_path: str,
    ) -> list[EdgeInfo]:
        """Detect CSS override relationships between selectors in a file."""
        if len(records) < 2:
            return []

        # Build property → record indices
        prop_index: dict[str, list[int]] = {}
        for i, rec in enumerate(records):
            for prop in rec.properties:
                prop_index.setdefault(prop, []).append(i)

        seen_pairs: set[tuple[str, str, str]] = set()
        raw_edges: list[EdgeInfo] = []

        max_override_candidates = 50  # Cap pairwise comparisons per property

        for prop, rec_indices in prop_index.items():
            if len(rec_indices) < 2:
                continue
            # Cap to avoid O(n²) blow-up on large files with common properties
            candidates = rec_indices[:max_override_candidates]
            for i in range(len(candidates)):
                for j in range(i + 1, len(candidates)):
                    ri = records[rec_indices[i]]
                    rj = records[rec_indices[j]]
                    result = self._check_override(ri, rj, prop)
                    if result is None:
                        continue
                    winner, loser, mechanism = result
                    pair_key = (winner.qualified_name, loser.qualified_name, prop)
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    raw_edges.append(EdgeInfo(
                        kind="OVERRIDES",
                        source=winner.qualified_name,
                        target=loser.qualified_name,
                        file_path=file_path,
                        line=winner.line_start,
                        extra={
                            "properties": [prop],
                            "source_specificity": list(winner.specificity),
                            "target_specificity": list(loser.specificity),
                            "mechanism": mechanism,
                        },
                    ))

        # Consolidate: merge edges between same (source, target) pair
        consolidated: dict[tuple[str, str], EdgeInfo] = {}
        for edge in raw_edges:
            key = (edge.source, edge.target)
            if key in consolidated:
                consolidated[key].extra["properties"].extend(edge.extra["properties"])
            else:
                consolidated[key] = edge

        return list(consolidated.values())

    def _check_override(
        self,
        a: _SelectorRecord,
        b: _SelectorRecord,
        prop: str,
    ) -> Optional[tuple[_SelectorRecord, _SelectorRecord, str]]:
        """Check if two selectors override each other for a property.

        Returns (winner, loser, mechanism) or None.
        """
        a_imp = a.has_important.get(prop, False)
        b_imp = b.has_important.get(prop, False)
        if a_imp and not b_imp:
            return (a, b, "important")
        if b_imp and not a_imp:
            return (b, a, "important")

        # BEM refinement
        if self._is_bem_refinement(a.selector_text, b.selector_text):
            return (a, b, "bem_refinement")
        if self._is_bem_refinement(b.selector_text, a.selector_text):
            return (b, a, "bem_refinement")

        # Same selector, source order
        if a.selector_text == b.selector_text:
            if a.source_order > b.source_order:
                return (a, b, "source_order")
            return (b, a, "source_order")

        # Key selector match with different specificity
        key_a = self._extract_key_selector(a.selector_text)
        key_b = self._extract_key_selector(b.selector_text)
        if key_a and key_b and key_a == key_b:
            if a.specificity > b.specificity:
                return (a, b, "specificity")
            elif b.specificity > a.specificity:
                return (b, a, "specificity")

        return None

    @staticmethod
    def _is_bem_refinement(candidate: str, base: str) -> bool:
        """Check if candidate is a BEM refinement of base.

        Only applies to simple class selectors (e.g. .btn → .btn-primary).
        """
        candidate = candidate.strip()
        base = base.strip()
        if not base or not candidate or len(candidate) <= len(base):
            return False
        # Only match simple class selectors (no spaces, combinators, etc.)
        if " " in base or ">" in base or "+" in base or "~" in base:
            return False
        if candidate.startswith(base):
            next_char = candidate[len(base)]
            return next_char in ("-", "_", ".")
        return False

    @staticmethod
    def _extract_key_selector(selector_text: str) -> Optional[str]:
        """Extract the rightmost simple selector (key selector)."""
        parts = re.split(r"\s*[>+~]\s*|\s+", selector_text.strip())
        return parts[-1] if parts else None

    def _split_css_selectors(
        self, selectors_node,
    ) -> list[tuple[str, object]]:
        """Split comma-separated selectors into (text, AST node) tuples."""
        result: list[tuple[str, object]] = []
        current_parts: list = []
        for child in selectors_node.children:
            if child.type == ",":
                if current_parts:
                    sel_text = self._normalize_selector_text(current_parts)
                    result.append((sel_text, current_parts[-1]))
                    current_parts = []
            else:
                current_parts.append(child)
        if current_parts:
            sel_text = self._normalize_selector_text(current_parts)
            result.append((sel_text, current_parts[-1]))
        return result

    @staticmethod
    def _normalize_selector_text(parts: list) -> str:
        """Build normalized selector text from AST node parts."""
        texts = []
        for part in parts:
            raw = part.text.decode("utf-8", errors="replace").strip()
            raw = re.sub(r"\s+", " ", raw)
            texts.append(raw)
        return " ".join(texts)

    @staticmethod
    def _extract_css_import(node) -> Optional[str]:
        """Extract the import target from a CSS @import statement."""
        for child in node.children:
            if child.type == "string_value":
                raw = child.text.decode("utf-8", errors="replace")
                return raw.strip("'\"")
            if child.type == "call_expression":
                # url('path')
                for sub in child.children:
                    if sub.type == "arguments":
                        for arg in sub.children:
                            if arg.type == "string_value":
                                raw = arg.text.decode("utf-8", errors="replace")
                                return raw.strip("'\"")
        return None

    @staticmethod
    def _get_css_media_text(node) -> str:
        """Extract normalized media query text from a media_statement node."""
        parts = []
        for child in node.children:
            if child.type in ("feature_query", "keyword_query", "binary_query"):
                raw = child.text.decode("utf-8", errors="replace")
                parts.append(re.sub(r"\s+", "", raw))
        return ",".join(parts) if parts else "unknown"

    @staticmethod
    def _get_css_property_name(decl_node) -> Optional[str]:
        """Extract property name from a CSS declaration node."""
        for child in decl_node.children:
            if child.type == "property_name":
                return child.text.decode("utf-8", errors="replace")
            if child.type == "variable_name":
                return child.text.decode("utf-8", errors="replace")
        return None

    @staticmethod
    def _find_var_refs(decl_node) -> list[str]:
        """Find all var(--name) references in a declaration value."""
        refs: list[str] = []
        stack = list(decl_node.children)
        while stack:
            n = stack.pop()
            if n.type == "call_expression":
                fn_name = None
                for child in n.children:
                    if child.type == "function_name":
                        fn_name = child.text.decode("utf-8", errors="replace")
                    elif child.type == "arguments" and fn_name == "var":
                        for arg in child.children:
                            if arg.type in ("plain_value", "identifier"):
                                val = arg.text.decode("utf-8", errors="replace")
                                if val.startswith("--"):
                                    refs.append(val)
            for child in n.children:
                stack.append(child)
        return refs

    @staticmethod
    def _has_important(decl_node) -> bool:
        """Check if a CSS declaration has !important."""
        stack = list(decl_node.children)
        while stack:
            n = stack.pop()
            if n.type == "important":
                return True
            for child in n.children:
                stack.append(child)
        return False

    # --- LESS regex-based parser (v2) ---

    # Regex patterns for LESS parsing (no tree-sitter grammar available)
    _LESS_SELECTOR_RE = re.compile(
        r"^([.#\w][\w\-\s>+~,.:[\]=*^$|\"'()]*?)\s*\{", re.MULTILINE,
    )
    _LESS_VARIABLE_RE = re.compile(r"^(@[\w-]+)\s*:\s*(.+?);", re.MULTILINE)
    _LESS_IMPORT_RE = re.compile(
        r'@import\s+(?:\([^)]+\)\s+)?["\']([^"\']+)["\']', re.MULTILINE,
    )
    _LESS_MIXIN_DEF_RE = re.compile(
        r"^([.#][\w-]+)\s*\(([^)]*)\)\s*\{", re.MULTILINE,
    )
    _LESS_MIXIN_CALL_RE = re.compile(
        r"^\s*([.#][\w-]+)\s*\(([^)]*)\)\s*;", re.MULTILINE,
    )

    @staticmethod
    def _less_specificity(selector_text: str) -> tuple[int, int, int]:
        """Compute CSS specificity from selector text using regex.

        Returns (a, b, c) where a=IDs, b=classes/attrs/pseudo-classes,
        c=type selectors/pseudo-elements.
        """
        # Strip pseudo-elements first (::before, ::after, etc.)
        stripped = re.sub(r"::[a-zA-Z-]+", "", selector_text)
        a = len(re.findall(r"#[\w-]+", stripped))
        b = len(re.findall(r"\.[\w-]+", stripped))
        b += len(re.findall(r"\[[\w-]+", stripped))
        b += len(re.findall(r":[\w-]+", stripped))
        # Type selectors: standalone word not preceded by . # : [
        c = len(re.findall(r"(?<![.#:\[])(?:^|[\s>+~])([a-zA-Z][\w]*)", stripped))
        return (a, b, c)

    def _parse_less(
        self, path: Path, source: bytes, line_offset: int = 0,
    ) -> tuple[list[NodeInfo], list[EdgeInfo]]:
        """Parse a LESS file using regex-based extraction.

        Creates same NodeInfo/EdgeInfo structures as _parse_css().
        Supports selectors, @variables, @import, mixins, and mixin calls.
        Note: nesting is not fully resolved (top-level selectors only).
        """
        file_path_str = str(path)
        text = source.decode("utf-8", errors="replace")
        nodes: list[NodeInfo] = []
        edges: list[EdgeInfo] = []
        selector_records: list[CodeParser._SelectorRecord] = []

        # File node
        nodes.append(NodeInfo(
            kind="File",
            name=file_path_str,
            file_path=file_path_str,
            line_start=1 + line_offset,
            line_end=text.count("\n") + 1 + line_offset,
            language="less",
        ))

        source_order = 0

        # Extract @import statements
        for m in self._LESS_IMPORT_RE.finditer(text):
            target = m.group(1)
            line = text[:m.start()].count("\n") + 1 + line_offset
            edges.append(EdgeInfo(
                kind="IMPORTS_FROM",
                source=file_path_str,
                target=target,
                file_path=file_path_str,
                line=line,
            ))

        # Extract @variable declarations
        for m in self._LESS_VARIABLE_RE.finditer(text):
            var_name = m.group(1)
            line = text[:m.start()].count("\n") + 1 + line_offset
            qualified = self._qualify(var_name, file_path_str, None)
            nodes.append(NodeInfo(
                kind="Function",
                name=var_name,
                file_path=file_path_str,
                line_start=line,
                line_end=line,
                language="less",
                extra={"css_kind": "less_variable"},
            ))
            edges.append(EdgeInfo(
                kind="CONTAINS",
                source=file_path_str,
                target=qualified,
                file_path=file_path_str,
                line=line,
            ))

        # Extract mixin definitions (before selectors to avoid double-matching)
        mixin_names: set[str] = set()
        for m in self._LESS_MIXIN_DEF_RE.finditer(text):
            mixin_name = m.group(1)
            params = m.group(2).strip()
            line = text[:m.start()].count("\n") + 1 + line_offset
            mixin_names.add(mixin_name)
            qualified = self._qualify(mixin_name, file_path_str, None)
            nodes.append(NodeInfo(
                kind="Function",
                name=mixin_name,
                file_path=file_path_str,
                line_start=line,
                line_end=line,
                language="less",
                params=params if params else None,
                extra={"css_kind": "mixin"},
            ))
            edges.append(EdgeInfo(
                kind="CONTAINS",
                source=file_path_str,
                target=qualified,
                file_path=file_path_str,
                line=line,
            ))

        # Extract selectors (skip lines that are mixin definitions)
        for m in self._LESS_SELECTOR_RE.finditer(text):
            sel_text = m.group(1).strip()
            if not sel_text:
                continue
            # Skip if this is a mixin definition (has parens)
            full_line = text[m.start():m.end()]
            if re.search(r"\(", full_line.split("{")[0]):
                continue
            # Skip @-rules
            if sel_text.startswith("@"):
                continue

            line_start = text[:m.start()].count("\n") + 1 + line_offset
            # Split comma-separated selectors
            for individual in sel_text.split(","):
                individual = individual.strip()
                if not individual:
                    continue
                specificity = self._less_specificity(individual)
                qualified = self._qualify(individual, file_path_str, None)
                nodes.append(NodeInfo(
                    kind="Class",
                    name=individual,
                    file_path=file_path_str,
                    line_start=line_start,
                    line_end=line_start,
                    language="less",
                    extra={
                        "css_kind": "selector",
                        "specificity": list(specificity),
                    },
                ))
                edges.append(EdgeInfo(
                    kind="CONTAINS",
                    source=file_path_str,
                    target=qualified,
                    file_path=file_path_str,
                    line=line_start,
                ))
                # Collect for override detection
                selector_records.append(self._SelectorRecord(
                    selector_text=individual,
                    qualified_name=qualified,
                    specificity=specificity,
                    properties={},
                    has_important={},
                    line_start=line_start,
                    line_end=line_start,
                    source_order=source_order,
                ))
                source_order += 1

        # Extract mixin calls
        for m in self._LESS_MIXIN_CALL_RE.finditer(text):
            call_name = m.group(1)
            line = text[:m.start()].count("\n") + 1 + line_offset
            edges.append(EdgeInfo(
                kind="CALLS",
                source=file_path_str,
                target=self._qualify(call_name, file_path_str, None),
                file_path=file_path_str,
                line=line,
            ))

        # Detect overrides among selectors
        override_edges = self._detect_css_overrides(
            selector_records, file_path_str,
        )
        edges.extend(override_edges)

        return nodes, edges

    # --- JSX / Vue template class extraction (v2) ---

    @staticmethod
    def _camel_to_kebab(name: str) -> str:
        """Convert camelCase to kebab-case for CSS Modules resolution.

        Examples: btnPrimary → btn-primary, navItem → nav-item
        """
        return re.sub(r"(?<=[a-z0-9])([A-Z])", r"-\1", name).lower()

    def _extract_jsx_class_refs(
        self, attr_node, source: bytes,
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """Extract class names from a jsx_attribute node.

        Returns (static_classes, module_refs) where module_refs are
        (import_name, property_name) tuples for CSS Modules.
        """
        static_classes: list[str] = []
        module_refs: list[tuple[str, str]] = []

        # Check this is a className or class attribute
        attr_name = None
        value_node = None
        for child in attr_node.children:
            if child.type == "property_identifier":
                attr_name = child.text.decode("utf-8", errors="replace")
            elif child.type == "string":
                value_node = child
            elif child.type == "jsx_expression":
                value_node = child

        if attr_name not in ("className", "class"):
            return static_classes, module_refs

        if value_node is None:
            return static_classes, module_refs

        if value_node.type == "string":
            # className="btn btn-primary"
            text = value_node.text.decode("utf-8", errors="replace")
            text = text.strip("'\"")
            static_classes.extend(text.split())
        elif value_node.type == "jsx_expression":
            # Look inside the expression
            for expr_child in value_node.children:
                if expr_child.type == "string":
                    # className={"btn btn-primary"}
                    text = expr_child.text.decode("utf-8", errors="replace")
                    text = text.strip("'\"")
                    static_classes.extend(text.split())
                elif expr_child.type == "template_string":
                    # className={`btn ${...}`} — extract literal parts
                    for frag in expr_child.children:
                        if frag.type == "string_fragment":
                            for part in frag.text.decode(
                                "utf-8", errors="replace",
                            ).split():
                                if part and not part.startswith("$"):
                                    static_classes.append(part)
                elif expr_child.type == "member_expression":
                    # className={styles.btnPrimary}
                    obj_name = None
                    prop_name = None
                    for me_child in expr_child.children:
                        if me_child.type == "identifier":
                            obj_name = me_child.text.decode(
                                "utf-8", errors="replace",
                            )
                        elif me_child.type == "property_identifier":
                            prop_name = me_child.text.decode(
                                "utf-8", errors="replace",
                            )
                    if obj_name and prop_name:
                        module_refs.append((obj_name, prop_name))

        return static_classes, module_refs

    def _extract_vue_template_classes(self, node) -> list[str]:
        """Recursively walk Vue template AST, extract class='...' values.

        Only extracts static class attributes, skips :class dynamic bindings.
        """
        classes: list[str] = []
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "attribute":
                attr_name = None
                attr_value = None
                for child in n.children:
                    if child.type == "attribute_name":
                        attr_name = child.text.decode(
                            "utf-8", errors="replace",
                        )
                    elif child.type == "quoted_attribute_value":
                        for v in child.children:
                            if v.type == "attribute_value":
                                attr_value = v.text.decode(
                                    "utf-8", errors="replace",
                                )
                if attr_name == "class" and attr_value:
                    classes.extend(attr_value.split())
            # Skip directive_attribute (:class bindings)
            elif n.type == "directive_attribute":
                continue
            for child in n.children:
                stack.append(child)
        return classes

    def _resolve_call_targets(
        self,
        nodes: list[NodeInfo],
        edges: list[EdgeInfo],
        file_path: str,
    ) -> list[EdgeInfo]:
        """Resolve bare call targets to qualified names using same-file definitions.

        After parsing, CALLS edges store bare function names (e.g. ``FirebaseAuth``)
        as targets. This method builds a symbol table from the parsed nodes and
        qualifies any bare target that matches a local definition, so that
        ``callers_of`` / ``callees_of`` queries produce correct results.

        External calls (names not defined in this file) remain bare.
        """
        # Build symbol table: bare_name -> qualified_name
        symbols: dict[str, str] = {}
        for node in nodes:
            if node.kind in ("Function", "Class", "Type", "Test"):
                bare = node.name
                qualified = self._qualify(bare, file_path, node.parent_name)
                if bare not in symbols:
                    symbols[bare] = qualified

        resolved: list[EdgeInfo] = []
        for edge in edges:
            if edge.kind == "CALLS" and "::" not in edge.target:
                if edge.target in symbols:
                    edge = EdgeInfo(
                        kind=edge.kind,
                        source=edge.source,
                        target=symbols[edge.target],
                        file_path=edge.file_path,
                        line=edge.line,
                        extra=edge.extra,
                    )
            resolved.append(edge)
        return resolved

    _MAX_AST_DEPTH = 180  # Guard against pathologically nested source files

    def _extract_from_tree(
        self,
        root,
        source: bytes,
        language: str,
        file_path: str,
        nodes: list[NodeInfo],
        edges: list[EdgeInfo],
        enclosing_class: Optional[str] = None,
        enclosing_func: Optional[str] = None,
        import_map: Optional[dict[str, str]] = None,
        defined_names: Optional[set[str]] = None,
        _depth: int = 0,
        jsx_class_collector: Optional[dict[str, dict]] = None,
    ) -> None:
        """Recursively walk the AST and extract nodes/edges."""
        if _depth > self._MAX_AST_DEPTH:
            return
        class_types = set(_CLASS_TYPES.get(language, []))
        func_types = set(_FUNCTION_TYPES.get(language, []))
        import_types = set(_IMPORT_TYPES.get(language, []))
        call_types = set(_CALL_TYPES.get(language, []))

        for child in root.children:
            node_type = child.type

            # --- Classes ---
            if node_type in class_types:
                name = self._get_name(child, language, "class")
                if name:
                    node = NodeInfo(
                        kind="Class",
                        name=name,
                        file_path=file_path,
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        language=language,
                        parent_name=enclosing_class,
                    )
                    nodes.append(node)

                    # CONTAINS edge
                    edges.append(EdgeInfo(
                        kind="CONTAINS",
                        source=file_path,
                        target=self._qualify(name, file_path, enclosing_class),
                        file_path=file_path,
                        line=child.start_point[0] + 1,
                    ))

                    # Inheritance edges
                    bases = self._get_bases(child, language, source)
                    for base in bases:
                        edges.append(EdgeInfo(
                            kind="INHERITS",
                            source=self._qualify(name, file_path, enclosing_class),
                            target=base,
                            file_path=file_path,
                            line=child.start_point[0] + 1,
                        ))

                    # Recurse into class body
                    self._extract_from_tree(
                        child, source, language, file_path, nodes, edges,
                        enclosing_class=name, enclosing_func=None,
                        import_map=import_map, defined_names=defined_names,
                        _depth=_depth + 1,
                        jsx_class_collector=jsx_class_collector,
                    )
                    continue

            # --- Functions ---
            if node_type in func_types:
                name = self._get_name(child, language, "function")
                if name:
                    is_test = _is_test_function(name, file_path)
                    kind = "Test" if is_test else "Function"
                    qualified = self._qualify(name, file_path, enclosing_class)
                    params = self._get_params(child, language, source)
                    ret_type = self._get_return_type(child, language, source)

                    node = NodeInfo(
                        kind=kind,
                        name=name,
                        file_path=file_path,
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        language=language,
                        parent_name=enclosing_class,
                        params=params,
                        return_type=ret_type,
                        is_test=is_test,
                    )
                    nodes.append(node)

                    # CONTAINS edge
                    container = (
                        self._qualify(enclosing_class, file_path, None)
                        if enclosing_class
                        else file_path
                    )
                    edges.append(EdgeInfo(
                        kind="CONTAINS",
                        source=container,
                        target=qualified,
                        file_path=file_path,
                        line=child.start_point[0] + 1,
                    ))

                    # Solidity: modifier invocations on functions → CALLS edges
                    if language == "solidity":
                        for sub in child.children:
                            if sub.type == "modifier_invocation":
                                for ident in sub.children:
                                    if ident.type == "identifier":
                                        edges.append(EdgeInfo(
                                            kind="CALLS",
                                            source=qualified,
                                            target=ident.text.decode(
                                                "utf-8", errors="replace",
                                            ),
                                            file_path=file_path,
                                            line=sub.start_point[0] + 1,
                                        ))
                                        break

                    # Recurse to find calls inside the function
                    self._extract_from_tree(
                        child, source, language, file_path, nodes, edges,
                        enclosing_class=enclosing_class, enclosing_func=name,
                        import_map=import_map, defined_names=defined_names,
                        _depth=_depth + 1,
                        jsx_class_collector=jsx_class_collector,
                    )
                    continue

            # --- Imports ---
            if node_type in import_types:
                imports = self._extract_import(child, language, source)
                for imp_target in imports:
                    edges.append(EdgeInfo(
                        kind="IMPORTS_FROM",
                        source=file_path,
                        target=imp_target,
                        file_path=file_path,
                        line=child.start_point[0] + 1,
                    ))
                continue

            # --- Calls ---
            if node_type in call_types:
                call_name = self._get_call_name(child, language, source)
                if call_name and enclosing_func:
                    caller = self._qualify(enclosing_func, file_path, enclosing_class)
                    target = self._resolve_call_target(
                        call_name, file_path, language,
                        import_map or {}, defined_names or set(),
                    )
                    edges.append(EdgeInfo(
                        kind="CALLS",
                        source=caller,
                        target=target,
                        file_path=file_path,
                        line=child.start_point[0] + 1,
                    ))

            # --- JSX className extraction ---
            if (
                node_type == "jsx_attribute"
                and language in ("javascript", "typescript", "tsx")
                and jsx_class_collector is not None
            ):
                static_cls, mod_refs = self._extract_jsx_class_refs(
                    child, source,
                )
                func_key = self._qualify(
                    enclosing_func or "<module>", file_path, enclosing_class,
                )
                if static_cls or mod_refs:
                    if func_key not in jsx_class_collector:
                        jsx_class_collector[func_key] = {
                            "classes": [], "module_refs": [],
                        }
                    entry = jsx_class_collector[func_key]
                    entry["classes"].extend(static_cls)
                    entry["module_refs"].extend(mod_refs)

            # --- Solidity-specific constructs ---
            if language == "solidity":
                # Emit statements: emit EventName(...) → CALLS edge
                if node_type == "emit_statement" and enclosing_func:
                    for sub in child.children:
                        if sub.type == "expression":
                            for ident in sub.children:
                                if ident.type == "identifier":
                                    caller = self._qualify(
                                        enclosing_func, file_path, enclosing_class,
                                    )
                                    edges.append(EdgeInfo(
                                        kind="CALLS",
                                        source=caller,
                                        target=ident.text.decode("utf-8", errors="replace"),
                                        file_path=file_path,
                                        line=child.start_point[0] + 1,
                                    ))

                # State variable declarations → Function nodes (public ones
                # auto-generate getters, and all are critical for reviews)
                if node_type == "state_variable_declaration" and enclosing_class:
                    var_name = None
                    var_visibility = None
                    var_mutability = None
                    var_type = None
                    for sub in child.children:
                        if sub.type == "identifier":
                            var_name = sub.text.decode("utf-8", errors="replace")
                        elif sub.type == "visibility":
                            var_visibility = sub.text.decode("utf-8", errors="replace")
                        elif sub.type == "type_name":
                            var_type = sub.text.decode("utf-8", errors="replace")
                        elif sub.type in ("constant", "immutable"):
                            var_mutability = sub.type
                    if var_name:
                        qualified = self._qualify(var_name, file_path, enclosing_class)
                        nodes.append(NodeInfo(
                            kind="Function",
                            name=var_name,
                            file_path=file_path,
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            language=language,
                            parent_name=enclosing_class,
                            return_type=var_type,
                            modifiers=var_visibility,
                            extra={
                                "solidity_kind": "state_variable",
                                "mutability": var_mutability,
                            },
                        ))
                        edges.append(EdgeInfo(
                            kind="CONTAINS",
                            source=self._qualify(
                                enclosing_class, file_path, None,
                            ),
                            target=qualified,
                            file_path=file_path,
                            line=child.start_point[0] + 1,
                        ))
                        continue

                # File-level and contract-level constant declarations
                if node_type == "constant_variable_declaration":
                    var_name = None
                    var_type = None
                    for sub in child.children:
                        if sub.type == "identifier":
                            var_name = sub.text.decode("utf-8", errors="replace")
                        elif sub.type == "type_name":
                            var_type = sub.text.decode("utf-8", errors="replace")
                    if var_name:
                        qualified = self._qualify(
                            var_name, file_path, enclosing_class,
                        )
                        nodes.append(NodeInfo(
                            kind="Function",
                            name=var_name,
                            file_path=file_path,
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            language=language,
                            parent_name=enclosing_class,
                            return_type=var_type,
                            extra={"solidity_kind": "constant"},
                        ))
                        container = (
                            self._qualify(enclosing_class, file_path, None)
                            if enclosing_class
                            else file_path
                        )
                        edges.append(EdgeInfo(
                            kind="CONTAINS",
                            source=container,
                            target=qualified,
                            file_path=file_path,
                            line=child.start_point[0] + 1,
                        ))
                        continue

                # Using directives: using LibName for Type → DEPENDS_ON edge
                if node_type == "using_directive":
                    lib_name = None
                    for sub in child.children:
                        if sub.type == "type_alias":
                            for ident in sub.children:
                                if ident.type == "identifier":
                                    lib_name = ident.text.decode(
                                        "utf-8", errors="replace",
                                    )
                    if lib_name:
                        source_name = (
                            self._qualify(enclosing_class, file_path, None)
                            if enclosing_class
                            else file_path
                        )
                        edges.append(EdgeInfo(
                            kind="DEPENDS_ON",
                            source=source_name,
                            target=lib_name,
                            file_path=file_path,
                            line=child.start_point[0] + 1,
                        ))
                    continue

            # Recurse for other node types
            self._extract_from_tree(
                child, source, language, file_path, nodes, edges,
                enclosing_class=enclosing_class, enclosing_func=enclosing_func,
                import_map=import_map, defined_names=defined_names,
                _depth=_depth + 1,
                jsx_class_collector=jsx_class_collector,
            )

    def _collect_file_scope(
        self, root, language: str, source: bytes,
    ) -> tuple[dict[str, str], set[str]]:
        """Pre-scan top-level AST to collect import mappings and defined names.

        Returns:
            (import_map, defined_names) where import_map maps imported names
            to their source module/path, and defined_names is the set of
            function/class names defined at file scope.
        """
        import_map: dict[str, str] = {}
        defined_names: set[str] = set()

        class_types = set(_CLASS_TYPES.get(language, []))
        func_types = set(_FUNCTION_TYPES.get(language, []))
        import_types = set(_IMPORT_TYPES.get(language, []))

        # Node types that wrap a class/function with decorators/annotations
        decorator_wrappers = {"decorated_definition", "decorator"}

        for child in root.children:
            node_type = child.type

            # Unwrap decorator wrappers to reach the inner definition
            target = child
            if node_type in decorator_wrappers:
                for inner in child.children:
                    if inner.type in func_types or inner.type in class_types:
                        target = inner
                        break

            target_type = target.type

            # Collect defined function/class names
            if target_type in func_types or target_type in class_types:
                name = self._get_name(target, language,
                                      "class" if target_type in class_types else "function")
                if name:
                    defined_names.add(name)

            # Collect import mappings: imported_name → module_path
            if node_type in import_types:
                self._collect_import_names(child, language, source, import_map)

        return import_map, defined_names

    def _collect_import_names(
        self, node, language: str, source: bytes, import_map: dict[str, str],
    ) -> None:
        """Extract imported names and their source modules into import_map."""
        if language == "python":
            if node.type == "import_from_statement":
                # from X.Y import A, B → {A: X.Y, B: X.Y}
                module = None
                seen_import_keyword = False
                for child in node.children:
                    if child.type == "dotted_name" and not seen_import_keyword:
                        module = child.text.decode("utf-8", errors="replace")
                    elif child.type == "import":
                        seen_import_keyword = True
                    elif seen_import_keyword and module:
                        if child.type in ("identifier", "dotted_name"):
                            name = child.text.decode("utf-8", errors="replace")
                            import_map[name] = module
                        elif child.type == "aliased_import":
                            # from X import A as B → {B: X}
                            names = [
                                sub.text.decode("utf-8", errors="replace")
                                for sub in child.children
                                if sub.type in ("identifier", "dotted_name")
                            ]
                            # Last name is the alias (local name)
                            if names:
                                import_map[names[-1]] = module

        elif language in ("javascript", "typescript", "tsx"):
            # import { A, B } from './path' → {A: ./path, B: ./path}
            module = None
            for child in node.children:
                if child.type == "string":
                    module = child.text.decode("utf-8", errors="replace").strip("'\"")
            if module:
                for child in node.children:
                    if child.type == "import_clause":
                        self._collect_js_import_names(child, module, import_map)

    def _collect_js_import_names(
        self, clause_node, module: str, import_map: dict[str, str],
    ) -> None:
        """Walk JS/TS import_clause to extract named and default imports."""
        for child in clause_node.children:
            if child.type == "identifier":
                # Default import
                import_map[child.text.decode("utf-8", errors="replace")] = module
            elif child.type == "named_imports":
                for spec in child.children:
                    if spec.type == "import_specifier":
                        # Could be: name or name as alias
                        names = [
                            s.text.decode("utf-8", errors="replace")
                            for s in spec.children
                            if s.type in ("identifier", "property_identifier")
                        ]
                        # Last identifier is the local name
                        if names:
                            import_map[names[-1]] = module

    def _resolve_module_to_file(
        self, module: str, file_path: str, language: str,
    ) -> Optional[str]:
        """Resolve a module/import path to an absolute file path.

        Uses self._module_file_cache to avoid repeated filesystem lookups.
        """
        caller_dir = str(Path(file_path).parent)
        cache_key = f"{language}:{caller_dir}:{module}"
        if cache_key in self._module_file_cache:
            return self._module_file_cache[cache_key]

        resolved = self._do_resolve_module(module, file_path, language)
        if len(self._module_file_cache) >= self._MODULE_CACHE_MAX:
            self._module_file_cache.clear()
        self._module_file_cache[cache_key] = resolved
        return resolved

    def _do_resolve_module(
        self, module: str, file_path: str, language: str,
    ) -> Optional[str]:
        """Language-aware module-to-file resolution."""
        caller_dir = Path(file_path).parent

        if language == "python":
            rel_path = module.replace(".", "/")
            candidates = [rel_path + ".py", rel_path + "/__init__.py"]
            # Walk up from caller's directory to find the module file
            current = caller_dir
            while True:
                for candidate in candidates:
                    target = current / candidate
                    if target.is_file():
                        return str(target.resolve())
                if current == current.parent:
                    break
                current = current.parent

        elif language in ("javascript", "typescript", "tsx", "vue"):
            if module.startswith("."):
                # Relative import — resolve from caller's directory
                base = caller_dir / module
                extensions = [".ts", ".tsx", ".js", ".jsx", ".vue", ".css", ".scss"]
                # Try exact path first (might already have extension)
                if base.is_file():
                    return str(base.resolve())
                # Try with extensions
                for ext in extensions:
                    target = base.with_suffix(ext)
                    if target.is_file():
                        return str(target.resolve())
                # Try index file in directory
                if base.is_dir():
                    for ext in extensions:
                        target = base / f"index{ext}"
                        if target.is_file():
                            return str(target.resolve())

        elif language in ("css", "scss"):
            if module.startswith(".") or module.startswith("/"):
                base = caller_dir / module
                extensions = [".css", ".scss"]
                if base.is_file():
                    return str(base.resolve())
                for ext in extensions:
                    target = base.with_suffix(ext)
                    if target.is_file():
                        return str(target.resolve())
                # SCSS partial: _filename.scss
                base_name = base.name
                for ext in extensions:
                    partial = base.parent / f"_{base_name}{ext}"
                    if partial.is_file():
                        return str(partial.resolve())

        return None

    def _resolve_call_target(
        self,
        call_name: str,
        file_path: str,
        language: str,
        import_map: dict[str, str],
        defined_names: set[str],
    ) -> str:
        """Resolve a bare call name to a qualified target, with fallback."""
        if call_name in defined_names:
            return self._qualify(call_name, file_path, None)
        if call_name in import_map:
            resolved = self._resolve_module_to_file(
                import_map[call_name], file_path, language,
            )
            if resolved:
                return self._qualify(call_name, resolved, None)
        return call_name

    def _qualify(self, name: str, file_path: str, enclosing_class: Optional[str]) -> str:
        """Create a qualified name: file_path::ClassName.name or file_path::name."""
        if enclosing_class:
            return f"{file_path}::{enclosing_class}.{name}"
        return f"{file_path}::{name}"

    def _get_name(self, node, language: str, kind: str) -> Optional[str]:
        """Extract the name from a class/function definition node."""
        # Solidity: constructor and receive/fallback have no identifier child
        if language == "solidity":
            if node.type == "constructor_definition":
                return "constructor"
            if node.type == "fallback_receive_definition":
                for child in node.children:
                    if child.type in ("receive", "fallback"):
                        return child.text.decode("utf-8", errors="replace")
        # For C/C++: function names are inside function_declarator/pointer_declarator
        # Check these first to avoid matching the return type_identifier
        if language in ("c", "cpp") and kind == "function":
            for child in node.children:
                if child.type in ("function_declarator", "pointer_declarator"):
                    result = self._get_name(child, language, kind)
                    if result:
                        return result
        # Most languages use a 'name' child
        for child in node.children:
            if child.type in (
                "identifier", "name", "type_identifier", "property_identifier",
                "simple_identifier", "constant",
            ):
                return child.text.decode("utf-8", errors="replace")
        # For Go type declarations, look for type_spec
        if language == "go" and node.type == "type_declaration":
            for child in node.children:
                if child.type == "type_spec":
                    return self._get_name(child, language, kind)
        return None

    def _get_params(self, node, language: str, source: bytes) -> Optional[str]:
        """Extract parameter list as a string."""
        for child in node.children:
            if child.type in ("parameters", "formal_parameters", "parameter_list"):
                return child.text.decode("utf-8", errors="replace")
        # Solidity: parameters are direct children between ( and )
        if language == "solidity":
            params = [
                c.text.decode("utf-8", errors="replace")
                for c in node.children
                if c.type == "parameter"
            ]
            if params:
                return f"({', '.join(params)})"
        return None

    def _get_return_type(self, node, language: str, source: bytes) -> Optional[str]:
        """Extract return type annotation if present."""
        for child in node.children:
            if child.type in ("type", "return_type", "type_annotation", "return_type_definition"):
                return child.text.decode("utf-8", errors="replace")
        # Python: look for -> annotation
        if language == "python":
            for i, child in enumerate(node.children):
                if child.type == "->" and i + 1 < len(node.children):
                    return node.children[i + 1].text.decode("utf-8", errors="replace")
        return None

    def _get_bases(self, node, language: str, source: bytes) -> list[str]:
        """Extract base classes / implemented interfaces."""
        bases = []
        if language == "python":
            for child in node.children:
                if child.type == "argument_list":
                    for arg in child.children:
                        if arg.type in ("identifier", "attribute"):
                            bases.append(arg.text.decode("utf-8", errors="replace"))
        elif language in ("java", "csharp", "kotlin"):
            # Look for superclass/interfaces in extends/implements clauses
            for child in node.children:
                if child.type in (
                    "superclass", "super_interfaces", "extends_type",
                    "implements_type", "type_identifier", "supertype",
                    "delegation_specifier",
                ):
                    text = child.text.decode("utf-8", errors="replace")
                    bases.append(text)
        elif language == "cpp":
            # C++: base_class_clause contains type_identifiers
            for child in node.children:
                if child.type == "base_class_clause":
                    for sub in child.children:
                        if sub.type == "type_identifier":
                            bases.append(sub.text.decode("utf-8", errors="replace"))
        elif language in ("typescript", "javascript", "tsx"):
            # extends clause
            for child in node.children:
                if child.type in ("extends_clause", "implements_clause"):
                    for sub in child.children:
                        if sub.type in ("identifier", "type_identifier", "nested_identifier"):
                            bases.append(sub.text.decode("utf-8", errors="replace"))
        elif language == "solidity":
            # contract Foo is Bar, Baz { ... }
            for child in node.children:
                if child.type == "inheritance_specifier":
                    for sub in child.children:
                        if sub.type == "user_defined_type":
                            for ident in sub.children:
                                if ident.type == "identifier":
                                    bases.append(ident.text.decode("utf-8", errors="replace"))
        elif language == "go":
            # Embedded structs / interface composition
            for child in node.children:
                if child.type == "type_spec":
                    for sub in child.children:
                        if sub.type in ("struct_type", "interface_type"):
                            for field_node in sub.children:
                                if field_node.type == "field_declaration_list":
                                    for f in field_node.children:
                                        if f.type == "type_identifier":
                                            bases.append(f.text.decode("utf-8", errors="replace"))
        return bases

    def _extract_import(self, node, language: str, source: bytes) -> list[str]:
        """Extract import targets as module/path strings."""
        imports = []
        text = node.text.decode("utf-8", errors="replace").strip()

        if language == "python":
            # import x.y.z  or  from x.y import z
            if node.type == "import_from_statement":
                for child in node.children:
                    if child.type == "dotted_name":
                        imports.append(child.text.decode("utf-8", errors="replace"))
                        break
            else:
                for child in node.children:
                    if child.type == "dotted_name":
                        imports.append(child.text.decode("utf-8", errors="replace"))
        elif language in ("javascript", "typescript", "tsx"):
            # import ... from 'module'
            for child in node.children:
                if child.type == "string":
                    val = child.text.decode("utf-8", errors="replace").strip("'\"")
                    imports.append(val)
        elif language == "go":
            for child in node.children:
                if child.type == "import_spec_list":
                    for spec in child.children:
                        if spec.type == "import_spec":
                            for s in spec.children:
                                if s.type == "interpreted_string_literal":
                                    val = s.text.decode("utf-8", errors="replace")
                                    imports.append(val.strip('"'))
                elif child.type == "import_spec":
                    for s in child.children:
                        if s.type == "interpreted_string_literal":
                            val = s.text.decode("utf-8", errors="replace")
                            imports.append(val.strip('"'))
        elif language == "rust":
            # use crate::module::item
            imports.append(text.replace("use ", "").rstrip(";").strip())
        elif language in ("c", "cpp"):
            # #include <header> or #include "header"
            for child in node.children:
                if child.type in ("system_lib_string", "string_literal"):
                    val = child.text.decode("utf-8", errors="replace").strip("<>\"")
                    imports.append(val)
        elif language in ("java", "csharp"):
            # import/using package.Class
            parts = text.split()
            if len(parts) >= 2:
                imports.append(parts[-1].rstrip(";"))
        elif language == "solidity":
            # import "path/to/file.sol" or import {Symbol} from "path"
            for child in node.children:
                if child.type == "string":
                    val = child.text.decode("utf-8", errors="replace").strip('"')
                    if val:
                        imports.append(val)
        elif language == "ruby":
            # require 'module' or require_relative 'path'
            if "require" in text:
                match = re.search(r"""['"](.*?)['"]""", text)
                if match:
                    imports.append(match.group(1))
        else:
            # Fallback: just record the text
            imports.append(text)

        return imports

    def _get_call_name(self, node, language: str, source: bytes) -> Optional[str]:
        """Extract the function/method name being called."""
        if not node.children:
            return None

        first = node.children[0]

        # Solidity wraps call targets in an 'expression' node – unwrap it
        if language == "solidity" and first.type == "expression" and first.children:
            first = first.children[0]

        # Simple call: func_name(args)
        if first.type == "identifier":
            return first.text.decode("utf-8", errors="replace")

        # Method call: obj.method(args)
        member_types = (
            "attribute", "member_expression",
            "field_expression", "selector_expression",
        )
        if first.type in member_types:
            # Get the rightmost identifier (the method name)
            for child in reversed(first.children):
                if child.type in (
                    "identifier", "property_identifier", "field_identifier",
                    "field_name",
                ):
                    return child.text.decode("utf-8", errors="replace")
            return first.text.decode("utf-8", errors="replace")

        # Scoped call (e.g., Rust path::func())
        if first.type in ("scoped_identifier", "qualified_name"):
            return first.text.decode("utf-8", errors="replace")

        return None
