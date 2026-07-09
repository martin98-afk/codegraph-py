"""
Java language extraction configuration.
"""
from tree_sitter import Node as TSNode
from .base import LanguageConfig, ImportInfo


class JavaConfig(LanguageConfig):
    """Java extraction configuration."""

    language_id = 'java'

    function_types = []
    class_types = ['class_declaration']
    method_types = ['method_declaration']
    interface_types = ['interface_declaration']
    struct_types = []
    enum_types = ['enum_declaration']
    type_alias_types = []
    import_types = ['import_declaration']
    call_types = ['method_invocation']
    variable_types = ['variable_declaration', 'local_variable_declaration']
    field_types = ['field_declaration']
    property_types = []

    name_field = 'name'
    body_field = 'body'
    params_field = 'parameters'
    return_field = 'type'

    def get_signature(self, node: TSNode, source: bytes) -> str | None:
        params = node.child_by_field_name('parameters')
        if not params:
            return None
        sig = source[params.start_byte:params.end_byte].decode('utf-8', errors='replace')
        return_type = node.child_by_field_name('type')
        if return_type:
            type_text = source[return_type.start_byte:return_type.end_byte].decode('utf-8', errors='replace')
            sig = type_text + ' ' + sig
        return sig

    def extract_import(self, node: TSNode, source: bytes) -> ImportInfo | None:
        if node.type == 'import_declaration':
            text = source[node.start_byte:node.end_byte].decode('utf-8', errors='replace').strip()
            name = text.replace('import ', '').replace(';', '').strip()
            return ImportInfo(module_name=name, signature=text) if name else None
        return None

    def get_visibility(self, node: TSNode) -> str | None:
        # Walk previous siblings looking for modifier keywords
        prev = node.prev_sibling
        while prev is not None:
            if prev.type == 'public':
                return 'public'
            if prev.type == 'private':
                return 'private'
            if prev.type == 'protected':
                return 'protected'
            if prev.is_named:
                break
            prev = prev.prev_sibling
        return None

    def extract_extends(self, node: TSNode, source: bytes) -> list[str]:
        """Extract parent class name from Java 'extends' clause."""
        if node.type != 'class_declaration':
            return []
        superclass = node.child_by_field_name('superclass')
        if not superclass:
            return []
        # superclass contains 'extends' + type_identifier
        for child in superclass.named_children:
            if child.type in ('type_identifier', 'generic_type'):
                # For generic_type like 'Comparable<Dog>', get the type_identifier
                if child.type == 'generic_type':
                    for sub in child.named_children:
                        if sub.type == 'type_identifier':
                            return [source[sub.start_byte:sub.end_byte].decode('utf-8', errors='replace')]
                return [source[child.start_byte:child.end_byte].decode('utf-8', errors='replace')]
        return []

    def extract_implements(self, node: TSNode, source: bytes) -> list[str]:
        """Extract interface names from Java 'implements' clause."""
        if node.type != 'class_declaration':
            return []
        interfaces = node.child_by_field_name('interfaces')
        if not interfaces:
            return []
        result = []
        # interfaces is 'super_interfaces' containing type_list
        for child in interfaces.named_children:
            if child.type == 'type_list':
                for type_node in child.named_children:
                    if type_node.type == 'type_identifier':
                        result.append(source[type_node.start_byte:type_node.end_byte].decode('utf-8', errors='replace'))
                    elif type_node.type == 'generic_type':
                        for sub in type_node.named_children:
                            if sub.type == 'type_identifier':
                                result.append(source[sub.start_byte:sub.end_byte].decode('utf-8', errors='replace'))
                                break
        return result

    def extract_interface_extends(self, node: TSNode, source: bytes) -> list[str]:
        """Extract extended interface names from Java interface 'extends' clause."""
        if node.type != 'interface_declaration':
            return []
        result = []
        # Java uses 'extends_interfaces' as a named child of interface_declaration
        for child in node.named_children:
            if child.type == 'extends_interfaces':
                for type_node in child.named_children:
                    if type_node.type == 'type_list':
                        for tn in type_node.named_children:
                            if tn.type == 'type_identifier':
                                result.append(source[tn.start_byte:tn.end_byte].decode('utf-8', errors='replace'))
                            elif tn.type == 'generic_type':
                                for sub in tn.named_children:
                                    if sub.type == 'type_identifier':
                                        result.append(source[sub.start_byte:sub.end_byte].decode('utf-8', errors='replace'))
                                        break
        return result
