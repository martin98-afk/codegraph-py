"""
Language extraction configuration base types.

Mirrors the TypeScript LanguageExtractor interface from the original CodeGraph,
adapted for Python tree-sitter bindings.
"""

from typing import Optional, ClassVar
from tree_sitter import Node as TSNode

from ...types import NodeKind


class ImportInfo:
    """Information returned by a language's extract_import hook."""
    __slots__ = ('module_name', 'signature', 'handled_refs')

    def __init__(self, module_name: str, signature: str, handled_refs: bool = False):
        self.module_name = module_name
        self.signature = signature
        self.handled_refs = handled_refs


class VariableInfo:
    """Information about a variable within a declaration."""
    __slots__ = ('name', 'kind', 'signature')

    def __init__(self, name: str, kind: str, signature: Optional[str] = None):
        self.name = name
        self.kind = kind
        self.signature = signature


class LanguageConfig:
    """
    Language-specific extraction configuration.

    Each supported language defines class-level attributes for node type
    mappings and optional hooks. Subclasses automatically inherit and
    override parent config.
    """

    # --- Node type mappings ---
    function_types: ClassVar[list[str]] = []
    class_types: ClassVar[list[str]] = []
    method_types: ClassVar[list[str]] = []
    interface_types: ClassVar[list[str]] = []
    struct_types: ClassVar[list[str]] = []
    enum_types: ClassVar[list[str]] = []
    enum_member_types: ClassVar[list[str]] = []
    type_alias_types: ClassVar[list[str]] = []
    import_types: ClassVar[list[str]] = []
    call_types: ClassVar[list[str]] = []
    variable_types: ClassVar[list[str]] = []
    field_types: ClassVar[list[str]] = []
    property_types: ClassVar[list[str]] = []
    statement_types: ClassVar[list[str]] = []

    # --- Field name mappings ---
    name_field: ClassVar[str] = 'name'
    body_field: ClassVar[str] = 'body'
    params_field: ClassVar[str] = 'parameters'
    return_field: ClassVar[Optional[str]] = None

    # --- Language identity ---
    language_id: ClassVar[str] = ''
    """The tree-sitter language identifier (e.g. 'python', 'javascript')."""

    # ============================================================
    # Hooks — override in subclass for language-specific behaviour
    # ============================================================

    def get_signature(self, node: TSNode, source: bytes) -> Optional[str]:
        """Extract function/method signature."""
        return None

    def get_visibility(self, node: TSNode) -> Optional[str]:
        """Extract visibility (public/private/protected)."""
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        """Check if node is exported."""
        return False

    def is_async(self, node: TSNode) -> bool:
        """Check if node is async."""
        return False

    def is_static(self, node: TSNode) -> bool:
        """Check if node is static."""
        return False

    def is_const(self, node: TSNode) -> bool:
        """Check if variable declaration is a constant."""
        return False

    def extract_import(self, node: TSNode, source: bytes) -> Optional[ImportInfo]:
        """Extract import information from an import node."""
        return None

    def extract_variables(self, node: TSNode, source: bytes) -> list:
        """Extract variable declarations from a variable node."""
        return []

    def extract_modifiers(self, node: TSNode) -> list[str]:
        """Extract extra modifier keywords."""
        return []

    def get_name(self, node: TSNode, source: bytes) -> Optional[str]:
        """Custom name extraction for languages where name_field doesn't work.

        Called when the standard `child_by_field_name(name_field)` lookup fails.
        Override this for languages like C/C++ where the function name
        is nested inside a declarator chain.
        """
        return None

    def get_method_owner(self, node: TSNode, source: bytes) -> Optional[str]:
        """Extract the owner type name for a method from its AST node.

        Override for languages where methods are defined at file scope
        but associated with a type (e.g. Go methods with receiver).
        Returns the owner type name (e.g. 'Foo' for Go func (f *Foo) Bar()).
        """
        return None

    def classify_class_node(self, node: TSNode) -> str:
        """Classify a class_declaration node: 'class', 'struct', 'enum', 'interface', 'trait'."""
        return 'class'

    def extract_extends(self, node: TSNode, source: bytes) -> list[str]:
        """Extract parent class names (single inheritance).

        Returns a list of base class names. Most languages have at most one
        direct parent class; languages with multiple inheritance return more.
        Override in language config to parse 'extends' clauses.
        """
        return []

    def extract_implements(self, node: TSNode, source: bytes) -> list[str]:
        """Extract implemented interface names.

        Returns a list of interface/trait names this class implements.
        Override in language config to parse 'implements' / 'for' clauses.
        """
        return []

    def extract_interface_extends(self, node: TSNode, source: bytes) -> list[str]:
        """Extract parent interface names for interface declarations.

        Like extract_extends but for interfaces (which can extend multiple).
        Override in language config.
        """
        return []

    @staticmethod
    def should_skip_type(node_type: str) -> bool:
        """Check if a node type should be skipped during traversal."""
        return node_type in (
            'comment', 'string', 'string_content', 'escape_sequence',
            'interpolation',
        )

    # ============================================================
    # Convenience
    # ============================================================

    def all_symbol_types(self) -> set[str]:
        """Return the set of all AST node types that represent symbols."""
        return set(
            self.function_types + self.class_types + self.method_types
            + self.interface_types + self.struct_types + self.enum_types
            + self.type_alias_types + self.import_types + self.call_types
            + self.variable_types
        )
