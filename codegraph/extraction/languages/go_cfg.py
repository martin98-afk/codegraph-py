"""
Go language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class GoConfig(LanguageConfig):
    """Go extraction configuration."""

    language_id = 'go'

    function_types = ['function_declaration']
    class_types = []
    method_types = ['method_declaration']
    interface_types = ['interface_type']
    struct_types = ['struct_type']
    enum_types = []
    type_alias_types = ['type_spec']  # Go uses type_spec for type aliases and type definitions
    import_types = ['import_declaration', 'import_spec']
    call_types = ['call_expression']
    variable_types = ['var_declaration', 'short_var_declaration']
    field_types = ['field_declaration']
    property_types = []

    name_field = 'name'
    body_field = 'body'
    params_field = 'parameters'
    return_field = 'result'

    def get_signature(self, node: TSNode, source: bytes) -> str | None:
        params = node.child_by_field_name('parameters')
        if not params:
            return None
        sig = source[params.start_byte:params.end_byte].decode('utf-8', errors='replace')
        result = node.child_by_field_name('result')
        if result:
            sig += ' ' + source[result.start_byte:result.end_byte].decode('utf-8', errors='replace')
        return sig

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        if node.type == 'import_spec':
            path_node = node.child_by_field_name('path')
            if path_node:
                module_name = source[path_node.start_byte:path_node.end_byte].decode('utf-8', errors='replace')
                module_name = module_name.strip('"')
                return ImportInfo(
                    module_name=module_name,
                    signature=source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip(),
                )
        elif node.type == 'import_declaration':
            # Return parent container info
            return ImportInfo(module_name='', signature='import (...)')
        return None

    def get_method_owner(self, node: TSNode, source: bytes) -> str | None:
        """Extract receiver type name from Go method_declaration.

        For 'func (f *Foo) Bar()', returns 'Foo'.
        """
        if node.type != 'method_declaration':
            return None
        receiver = node.child_by_field_name('receiver')
        if not receiver:
            return None
        # Walk the type node to find the type_identifier text
        def _find_type_ident(n: TSNode) -> str | None:
            if n.type == 'type_identifier':
                try:
                    t = n.text
                    if isinstance(t, bytes):
                        return t.decode('utf-8', errors='replace')
                    return str(t)
                except Exception:
                    return None
            for child in n.named_children:
                result = _find_type_ident(child)
                if result is not None:
                    return result
            return None

        for child in receiver.named_children:
            if child.type == 'parameter_declaration':
                type_node = child.child_by_field_name('type')
                if type_node is not None:
                    owner = _find_type_ident(type_node)
                    if owner is not None:
                        return owner
                    # Fallback: raw text, strip leading *[]
                    txt = source[type_node.start_byte:type_node.end_byte].decode('utf-8', errors='replace')
                    return txt.lstrip('*[]')
        return None

    def is_exported(self, node: TSNode, source: bytes) -> bool:
        name = self._extract_name(node)
        return name is not None and name[0].isupper() if name else False

    def _extract_name(self, node: TSNode) -> str | None:
        name_node = node.child_by_field_name('name')
        if name_node:
            try:
                t = name_node.text
                if isinstance(t, bytes):
                    return t.decode('utf-8', errors='replace')
                return str(t)
            except Exception:
                pass
        return None
