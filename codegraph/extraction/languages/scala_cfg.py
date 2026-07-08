"""
Scala language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class ScalaConfig(LanguageConfig):
    """Scala extraction configuration."""

    language_id = 'scala'

    function_types = ['function_definition']
    class_types = ['class_definition']
    method_types = ['function_definition']  # Functions inside class/object bodies
    interface_types = ['trait_definition']
    struct_types = []
    enum_types = ['enum_definition']
    type_alias_types = ['type_definition']
    import_types = ['import_statement']
    call_types = ['call_expression']
    variable_types = ['val_definition', 'var_definition']
    field_types = ['variable_definition']
    property_types = []

    name_field = 'name'
    body_field = 'body'
    params_field = 'parameters'
    return_field = 'return_type'

    def get_signature(self, node: TSNode, source: bytes) -> str | None:
        params = node.child_by_field_name('parameters')
        if not params:
            return None
        sig = source[params.start_byte:params.end_byte].decode('utf-8', errors='replace')
        return_type = node.child_by_field_name('return_type')
        if return_type:
            ret = source[return_type.start_byte:return_type.end_byte].decode('utf-8', errors='replace')
            sig += f': {ret}'
        return sig

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        if node.type == 'import_statement':
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            path = node.child_by_field_name('path')
            if path:
                module_name = source[path.start_byte:path.end_byte].decode('utf-8', errors='replace')
                return ImportInfo(module_name=module_name, signature=text)
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        return True  # Scala defs at package level are public by default

    def is_static(self, node: TSNode) -> bool:
        # Check for 'static' modifier in Scala 3
        for child in node.children:
            if child.type == 'static':
                return True
        return False
