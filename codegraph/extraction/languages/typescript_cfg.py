"""
TypeScript/TSX language extraction configuration.
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


class TypeScriptConfig(LanguageConfig):
    """TypeScript/TSX extraction configuration."""

    language_id = 'typescript'

    # TS uses 'typescript' grammar, access specific dialects via 'typescript' or 'tsx'
    _tsx = False

    function_types = ['function_declaration', 'arrow_function', 'generator_function_declaration']
    class_types = ['class_declaration']
    method_types = ['method_definition']
    interface_types = ['interface_declaration']
    struct_types = []
    enum_types = ['enum_declaration']
    type_alias_types = ['type_alias_declaration']
    import_types = ['import_statement']
    call_types = ['call_expression']
    variable_types = ['variable_declaration', 'lexical_declaration']
    field_types = ['property_signature', 'public_field_definition']
    property_types = ['property_identifier']

    name_field = 'name'
    body_field = 'body'
    params_field = 'parameters'
    return_field = 'type'

    def get_signature(self, node: TSNode, source: bytes) -> str | None:
        params = node.child_by_field_name('parameters')
        if not params:
            return None
        sig = source[params.start_byte:params.end_byte].decode('utf-8', errors='replace')
        return_type = node.child_by_field_name('return_type')
        if return_type:
            sig += ': ' + source[return_type.start_byte:return_type.end_byte].decode('utf-8', errors='replace')
        return sig

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
        parent = node.parent
        if parent and parent.type == 'variable_declaration':
            kind = _node_text(parent.child(0)) if parent.child(0) else ''
            return kind == 'const'
        return False
