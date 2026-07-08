"""
JavaScript/JSX language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


def _node_text(node: TSNode) -> str:
    try:
        t = node.text
        if isinstance(t, bytes):
            return t.decode('utf-8', errors='replace')
        return str(t)
    except Exception:
        return ''


class JavaScriptConfig(LanguageConfig):
    """JavaScript/JSX extraction configuration."""

    language_id = 'javascript'

    function_types = ['function_declaration', 'arrow_function', 'generator_function_declaration']
    class_types = ['class_declaration']
    method_types = ['method_definition']
    interface_types = []
    struct_types = []
    enum_types = []
    type_alias_types = []
    import_types = ['import_statement', 'import_specifier']
    call_types = ['call_expression']
    variable_types = ['variable_declaration', 'lexical_declaration']
    field_types = ['field_definition']
    property_types = ['property_identifier']

    name_field = 'name'
    body_field = 'body'
    params_field = 'parameters'
    return_field = 'return_type'

    def get_signature(self, node: TSNode, source: bytes) -> str | None:
        params = node.child_by_field_name('parameters')
        if not params:
            return None
        return source[params.start_byte:params.end_byte].decode('utf-8', errors='replace')

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
        if node.type == 'import_statement':
            source_node = node.child_by_field_name('source')
            if source_node:
                module_name = source[source_node.start_byte:source_node.end_byte].decode('utf-8', errors='replace')
                module_name = module_name.strip('\'"')
                return ImportInfo(module_name=module_name, signature=text)
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        # Check if preceded by 'export' keyword
        prev = node.prev_sibling
        while prev is not None:
            if prev.type == 'export':
                return True
            if prev.is_named or prev.type in (';', '\n'):
                break
            prev = prev.prev_sibling
        return False

    def is_async(self, node: TSNode) -> bool:
        prev = node.prev_sibling
        while prev is not None:
            if prev.type == 'async':
                return True
            if prev.is_named:
                break
            prev = prev.prev_sibling
        return False

    def is_const(self, node: TSNode) -> bool:
        # Check variable_declarator for const keyword
        parent = node.parent
        if parent and parent.type == 'variable_declaration':
            kind = _node_text(parent.child(0)) if parent.child(0) else ''
            return kind == 'const'
        return False
