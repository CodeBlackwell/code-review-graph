"""Tests for Go, Rust, Java, C, C++, C#, Ruby, PHP, Kotlin, Swift, Solidity, Vue, CSS, and SCSS parsing."""

from pathlib import Path

import pytest

from code_review_graph.parser import CodeParser

FIXTURES = Path(__file__).parent / "fixtures"


class TestGoParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample_go.go")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("main.go")) == "go"

    def test_finds_structs_and_interfaces(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "User" in names
        assert "InMemoryRepo" in names
        assert "UserRepository" in names

    def test_finds_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "NewInMemoryRepo" in names
        assert "CreateUser" in names

    def test_finds_imports(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        targets = {e.target for e in imports}
        assert "errors" in targets
        assert "fmt" in targets

    def test_finds_calls(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        assert len(calls) >= 1

    def test_finds_contains(self):
        contains = [e for e in self.edges if e.kind == "CONTAINS"]
        assert len(contains) >= 3


class TestRustParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample_rust.rs")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("lib.rs")) == "rust"

    def test_finds_structs_and_traits(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "User" in names
        assert "InMemoryRepo" in names

    def test_finds_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "new" in names
        assert "create_user" in names
        assert "find_by_id" in names
        assert "save" in names

    def test_finds_imports(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        assert len(imports) >= 1

    def test_finds_calls(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        assert len(calls) >= 3


class TestJavaParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "SampleJava.java")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("Main.java")) == "java"

    def test_finds_classes_and_interfaces(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "UserRepository" in names
        assert "User" in names
        assert "InMemoryRepo" in names
        assert "UserService" in names

    def test_finds_methods(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "findById" in names
        assert "save" in names
        assert "getUser" in names

    def test_finds_imports(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        assert len(imports) >= 2

    def test_finds_inheritance(self):
        inherits = [e for e in self.edges if e.kind == "INHERITS"]
        # InMemoryRepo implements UserRepository
        assert len(inherits) >= 1

    def test_finds_calls(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        assert len(calls) >= 3


class TestCParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample.c")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("main.c")) == "c"

    def test_finds_structs(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "User" in names

    def test_finds_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "print_user" in names
        assert "main" in names
        assert "create_user" in names

    def test_finds_imports(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        targets = {e.target for e in imports}
        assert "stdio.h" in targets


class TestCppParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample.cpp")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("main.cpp")) == "cpp"

    def test_finds_classes(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "Animal" in names
        assert "Dog" in names

    def test_finds_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "greet" in names or "main" in names

    def test_finds_inheritance(self):
        inherits = [e for e in self.edges if e.kind == "INHERITS"]
        assert len(inherits) >= 1


def _has_csharp_parser():
    try:
        import tree_sitter_language_pack as tslp
        tslp.get_parser("csharp")
        return True
    except (LookupError, ImportError):
        return False


@pytest.mark.skipif(not _has_csharp_parser(), reason="csharp tree-sitter grammar not installed")
class TestCSharpParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "Sample.cs")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("Program.cs")) == "csharp"

    def test_finds_classes_and_interfaces(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "User" in names
        assert "InMemoryRepo" in names

    def test_finds_methods(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "FindById" in names or "Save" in names


class TestRubyParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample.rb")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("app.rb")) == "ruby"

    def test_finds_classes(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "User" in names or "UserRepository" in names

    def test_finds_methods(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "initialize" in names or "find_by_id" in names or "save" in names


class TestPHPParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample.php")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("index.php")) == "php"

    def test_finds_classes(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "User" in names or "InMemoryRepo" in names

    def test_finds_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert len(names) > 0


class TestKotlinParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample.kt")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("Main.kt")) == "kotlin"

    def test_finds_classes(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "User" in names or "InMemoryRepo" in names

    def test_finds_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "createUser" in names or "findById" in names or "save" in names


class TestSwiftParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample.swift")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("App.swift")) == "swift"

    def test_finds_classes(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "User" in names or "InMemoryRepo" in names

    def test_finds_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "createUser" in names or "findById" in names or "save" in names


class TestSolidityParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample.sol")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("Vault.sol")) == "solidity"

    def test_finds_contracts_interfaces_libraries(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "StakingVault" in names
        assert "BoostedPool" in names
        assert "IStakingPool" in names
        assert "RewardMath" in names

    def test_finds_structs(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "StakerPosition" in names

    def test_finds_enums(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "PoolStatus" in names

    def test_finds_custom_errors(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "InsufficientStake" in names
        assert "PoolNotActive" in names

    def test_finds_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "stake" in names
        assert "unstake" in names
        assert "stakedBalance" in names
        assert "pendingBonus" in names

    def test_finds_constructors(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        constructors = [f for f in funcs if f.name == "constructor"]
        assert len(constructors) == 2  # StakingVault + BoostedPool

    def test_finds_modifiers(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "nonZero" in names
        assert "whenPoolActive" in names

    def test_finds_events(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "Staked" in names
        assert "Unstaked" in names
        assert "BonusClaimed" in names

    def test_finds_file_level_events(self):
        funcs = [
            n for n in self.nodes
            if n.kind == "Function" and n.parent_name is None
        ]
        names = {f.name for f in funcs}
        # file-level events declared outside any contract
        assert "Staked" in names or "Unstaked" in names

    def test_finds_user_defined_value_types(self):
        classes = [n for n in self.nodes if n.kind == "Class"]
        names = {c.name for c in classes}
        assert "Price" in names
        assert "PositionId" in names

    def test_finds_file_level_constants(self):
        constants = [
            n for n in self.nodes
            if n.extra.get("solidity_kind") == "constant"
        ]
        names = {c.name for c in constants}
        assert "MAX_SUPPLY" in names
        assert "ZERO_ADDRESS" in names

    def test_finds_free_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        free = [f for f in funcs if f.name == "protocolFee"]
        assert len(free) == 1
        assert free[0].parent_name is None

    def test_finds_using_directive(self):
        depends = [e for e in self.edges if e.kind == "DEPENDS_ON"]
        targets = {e.target for e in depends}
        assert "RewardMath" in targets

    def test_finds_selective_imports(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        targets = {e.target for e in imports}
        assert "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol" in targets

    def test_finds_state_variables(self):
        state_vars = [
            n for n in self.nodes
            if n.extra.get("solidity_kind") == "state_variable"
        ]
        names = {v.name for v in state_vars}
        assert "stakes" in names
        assert "totalStaked" in names
        assert "guardian" in names
        assert "status" in names
        assert "MIN_STAKE" in names
        assert "launchTime" in names
        assert "bonusRate" in names
        assert "assetPrice" in names

    def test_state_variable_types(self):
        state_vars = {
            n.name: n for n in self.nodes
            if n.extra.get("solidity_kind") == "state_variable"
        }
        assert state_vars["totalStaked"].return_type == "uint256"
        assert state_vars["guardian"].return_type == "address"
        assert state_vars["stakes"].modifiers == "public"

    def test_finds_receive_and_fallback(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "receive" in names
        assert "fallback" in names

    def test_finds_imports(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        targets = {e.target for e in imports}
        assert "@openzeppelin/contracts/token/ERC20/ERC20.sol" in targets
        assert "@openzeppelin/contracts/access/Ownable.sol" in targets

    def test_finds_inheritance(self):
        inherits = [e for e in self.edges if e.kind == "INHERITS"]
        pairs = {(e.source.split("::")[-1], e.target) for e in inherits}
        assert ("StakingVault", "ERC20") in pairs
        assert ("StakingVault", "Ownable") in pairs
        assert ("StakingVault", "IStakingPool") in pairs
        assert ("BoostedPool", "StakingVault") in pairs

    def test_finds_function_calls(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        targets = {e.target.split("::")[-1] if "::" in e.target else e.target for e in calls}
        assert "require" in targets
        assert "_mint" in targets
        assert "_burn" in targets
        assert "pendingBonus" in targets or "BoostedPool.pendingBonus" in targets

    def test_finds_emit_edges(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        # Targets may be qualified (e.g. "file::BoostedPool.BonusClaimed")
        target_basenames = {e.target.split("::")[-1].split(".")[-1] for e in calls}
        assert "Staked" in target_basenames
        assert "Unstaked" in target_basenames
        assert "BonusClaimed" in target_basenames

    def test_finds_modifier_invocations(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        # Extract (source_basename, target_basename) to handle qualified names
        target_basenames = {e.target.split("::")[-1].split(".")[-1] for e in calls}
        assert "nonZero" in target_basenames
        assert "whenPoolActive" in target_basenames

    def test_finds_constructor_modifier_invocations(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        target_basenames = {e.target.split("::")[-1].split(".")[-1] for e in calls}
        assert "ERC20" in target_basenames
        assert "Ownable" in target_basenames
        assert "StakingVault" in target_basenames

    def test_finds_contains(self):
        contains = [e for e in self.edges if e.kind == "CONTAINS"]
        targets = {e.target.split("::")[-1] for e in contains}
        assert "StakingVault" in targets
        assert "StakingVault.stake" in targets
        assert "StakingVault.stakes" in targets
        assert "StakingVault.Staked" not in targets  # Staked is file-level
        assert "BoostedPool.claimBonus" in targets

    def test_extracts_params(self):
        funcs = {
            n.name: n for n in self.nodes
            if n.kind == "Function" and n.parent_name == "RewardMath"
        }
        assert funcs["mulPrecise"].params == "(uint256 a, uint256 b)"

    def test_extracts_return_type(self):
        funcs = {
            n.name: n for n in self.nodes
            if n.kind == "Function" and n.parent_name == "RewardMath"
        }
        assert "uint256" in funcs["mulPrecise"].return_type


class TestVueParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample_vue.vue")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("App.vue")) == "vue"

    def test_finds_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "increment" in names
        assert "onSelectUser" in names
        assert "fetchUsers" in names

    def test_finds_imports(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        targets = {e.target for e in imports}
        assert "vue" in targets
        assert "./UserList.vue" in targets

    def test_finds_contains(self):
        contains = [e for e in self.edges if e.kind == "CONTAINS"]
        assert len(contains) >= 3

    def test_nodes_have_vue_language(self):
        for node in self.nodes:
            assert node.language == "vue"

    def test_finds_calls(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        assert len(calls) >= 1

    def test_vue_style_block_parsing(self):
        parser = CodeParser()
        vue_with_style = b"""<template><div>Hello</div></template>
<script setup>
const x = 1;
</script>
<style scoped>
.app { color: red; }
.app .btn { padding: 10px; }
</style>
"""
        nodes, edges = parser.parse_bytes(
            Path("/tmp/test_style.vue"), vue_with_style,
        )
        css_selectors = [
            n for n in nodes
            if n.kind == "Class" and n.extra.get("css_kind") == "selector"
        ]
        assert len(css_selectors) >= 2
        for sel in css_selectors:
            assert sel.language == "vue"


class TestCSSParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample.css")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("styles.css")) == "css"

    def test_finds_selectors(self):
        classes = [
            n for n in self.nodes
            if n.kind == "Class" and n.extra.get("css_kind") == "selector"
        ]
        names = {c.name for c in classes}
        assert ".btn" in names
        assert ".btn-primary" in names
        assert "#main-header" in names
        assert "body" in names

    def test_comma_separated_selectors_split(self):
        classes = [
            n for n in self.nodes
            if n.kind == "Class" and n.extra.get("css_kind") == "selector"
        ]
        names = {c.name for c in classes}
        assert "h1" in names
        assert "h2" in names

    def test_finds_custom_properties(self):
        funcs = [
            n for n in self.nodes
            if n.kind == "Function" and n.extra.get("css_kind") == "custom_property"
        ]
        names = {f.name for f in funcs}
        assert "--primary-color" in names
        assert "--font-size" in names
        assert "--spacing-md" in names

    def test_finds_media_queries(self):
        classes = [
            n for n in self.nodes
            if n.kind == "Class" and n.extra.get("css_kind") == "media_query"
        ]
        assert len(classes) >= 1

    def test_finds_keyframes(self):
        classes = [
            n for n in self.nodes
            if n.kind == "Class" and n.extra.get("css_kind") == "keyframes"
        ]
        names = {c.name for c in classes}
        assert "@keyframes(fadeIn)" in names

    def test_finds_imports(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        targets = {e.target for e in imports}
        assert "reset.css" in targets
        assert "components.css" in targets

    def test_finds_var_calls(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        # body calls --font-size and --primary-color
        assert len(calls) >= 2

    def test_finds_contains(self):
        contains = [e for e in self.edges if e.kind == "CONTAINS"]
        assert len(contains) >= 5

    def test_specificity_computed(self):
        classes = {
            n.name: n for n in self.nodes
            if n.kind == "Class" and n.extra.get("css_kind") == "selector"
            and n.parent_name is None  # top-level only
        }
        assert classes[".btn"].extra["specificity"] == [0, 1, 0]
        assert classes["#main-header"].extra["specificity"] == [1, 0, 0]
        assert classes["body"].extra["specificity"] == [0, 0, 1]

    def test_override_edges_exist(self):
        overrides = [e for e in self.edges if e.kind == "OVERRIDES"]
        assert len(overrides) >= 1

    def test_bem_override(self):
        overrides = [e for e in self.edges if e.kind == "OVERRIDES"]
        bem = [
            e for e in overrides
            if e.extra.get("mechanism") == "bem_refinement"
        ]
        assert len(bem) >= 1
        # .btn-primary overrides .btn
        targets = {e.target.split("::")[-1] for e in bem}
        assert ".btn" in targets

    def test_important_override(self):
        overrides = [e for e in self.edges if e.kind == "OVERRIDES"]
        important = [
            e for e in overrides
            if e.extra.get("mechanism") == "important"
        ]
        assert len(important) >= 1

    def test_nodes_have_css_language(self):
        for node in self.nodes:
            assert node.language == "css"


class TestSCSSParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(FIXTURES / "sample.scss")

    def test_detects_language(self):
        assert self.parser.detect_language(Path("styles.scss")) == "scss"

    def test_finds_mixins(self):
        funcs = [
            n for n in self.nodes
            if n.kind == "Function" and n.extra.get("css_kind") == "mixin"
        ]
        names = {f.name for f in funcs}
        assert "flex-center" in names
        assert "responsive" in names

    def test_finds_scss_variables(self):
        funcs = [
            n for n in self.nodes
            if n.kind == "Function" and n.extra.get("css_kind") == "scss_variable"
        ]
        names = {f.name for f in funcs}
        assert "$primary-color" in names
        assert "$font-size" in names
        assert "$spacing" in names

    def test_finds_include_calls(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        targets = {e.target for e in calls}
        assert any("flex-center" in t for t in targets)

    def test_finds_imports(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        targets = {e.target for e in imports}
        assert "variables" in targets
        assert "mixins" in targets

    def test_finds_selectors(self):
        classes = [
            n for n in self.nodes
            if n.kind == "Class" and n.extra.get("css_kind") == "selector"
        ]
        names = {c.name for c in classes}
        assert ".btn" in names
        assert ".card" in names

    def test_nodes_have_scss_language(self):
        for node in self.nodes:
            assert node.language == "scss"
