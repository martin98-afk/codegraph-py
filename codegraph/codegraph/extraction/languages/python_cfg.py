"""
Python language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class PythonConfig(LanguageConfig):
    """Python-specific extraction configuration."""

    language_id = 'python'

    function_types = ['function_definition']
    class_types = ['class_definition']
    method_types = ['function_definition']  # Methods are functions inside classes
    interface_types = []
    struct_types = []
    enum_types = []
    type_alias_types = []
    import_types = ['import_statement', 'import_from_statement']
    call_types = ['call']
    variable_types = ['assignment']
    field_types = []
    property_types = []

    name_field = 'name'
    body_field = 'body'
    params_field = 'parameters'
    return_field = 'return_type'

    def get_signature(self, node: TSNode, source: bytes) -> str | None:
        params = node.child_by_field_name('parameters')
        return_type = node.child_by_field_name('return_type')
        if not params:
            return None
        sig = source[params.start_byte:params.end_byte].decode('utf-8', errors='replace')
        if return_type:
            sig += ' -> ' + source[return_type.start_byte:return_type.end_byte].decode('utf-8', errors='replace')
        return sig

    def is_async(self, node: TSNode) -> bool:
        prev = node.prev_sibling
        return prev is not None and prev.type == 'async'

    def is_static(self, node: TSNode) -> bool:
        prev = node.prev_named_sibling
        if prev is not None and prev.type == 'decorator':
            text = _node_text(prev)
            return 'staticmethod' in text
        return False

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
        if node.type == 'import_from_statement':
            module_node = node.child_by_field_name('module_name')
            if module_node:
                module_name = source[module_node.start_byte:module_node.end_byte].decode('utf-8', errors='replace')
                return ImportInfo(module_name=module_name, signature=text)
        elif node.type == 'import_statement':
            name_node = node.child_by_field_name('name')
            if name_node:
                module_name = source[name_node.start_byte:name_node.end_byte].decode('utf-8', errors='replace')
                return ImportInfo(module_name=module_name, signature=text)
        return None


def _node_text(node: TSNode) -> str:
    """Get text from a tree-sitter node safely."""
    try:
        t = node.text
        if isinstance(t, bytes):
            return t.decode('utf-8', errors='replace')
        return str(t)
    except Exception:
        return ''
