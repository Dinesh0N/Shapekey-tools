bl_info = {
    "name": "Shape Key Value Transfer & Native Presets",
    "author": "Blender Dev",
    "version": (1, 2, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar (N) > ShapeKeys | Properties > Mesh Data",
    "description": "Copy/paste shape key values between blend files using Blender's native preset system and undo buffer",
    "category": "Mesh",
}

import bpy
import json
import os
from bpy.props import StringProperty, BoolProperty, PointerProperty
from bl_operators.presets import AddPresetBase
from bl_ui.utils import PresetPanel


PRESET_SUBDIR = "mesh_shapekeys"


# -------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------

def get_shapekey_dict(obj):
    """Extracts non-basis shape key name-value pairs."""
    if not obj or obj.type != 'MESH' or not obj.data or not obj.data.shape_keys:
        return {}
    key_blocks = obj.data.shape_keys.key_blocks
    ref_key = obj.data.shape_keys.reference_key
    return {kb.name: float(kb.value) for kb in key_blocks if kb != ref_key}


def apply_shapekey_dict(obj, data_dict):
    """Applies values to matching keys on the object."""
    if not obj or obj.type != 'MESH' or not obj.data or not obj.data.shape_keys:
        return 0

    key_blocks = obj.data.shape_keys.key_blocks
    count = 0
    for name, val in data_dict.items():
        if name in key_blocks:
            key_blocks[name].value = float(val)
            count += 1

    obj.data.update()
    return count


# -------------------------------------------------------------------
# Properties / Memory State
# -------------------------------------------------------------------

class ShapeKeyTransferSettings(bpy.types.PropertyGroup):
    old_values_json: StringProperty(name="Old Values JSON", default="{}")
    has_backup: BoolProperty(name="Has Backup", default=False)


# -------------------------------------------------------------------
# Native Preset Menu & Operators
# -------------------------------------------------------------------

class MESH_MT_shapekey_presets(bpy.types.Menu):
    """Native Preset dropdown menu"""
    bl_label = "Shape Key Presets"
    preset_subdir = PRESET_SUBDIR
    preset_operator = "script.execute_preset"
    draw = bpy.types.Menu.draw_preset


class MESH_OT_add_shapekey_preset(AddPresetBase, bpy.types.Operator):
    """Add or remove a preset file in Blender's default presets directory"""
    bl_idname = "mesh.shapekey_preset_add"
    bl_label = "Add Shape Key Preset"
    preset_menu = "MESH_MT_shapekey_presets"
    preset_subdir = PRESET_SUBDIR

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH' and obj.data and obj.data.shape_keys)

    def execute(self, context):
        # Native Preset system writes a Python script into ~/.config/blender/.../presets/
        if self.remove_active or self.remove_name:
            return super().execute(context)

        obj = context.active_object
        data = get_shapekey_dict(obj)
        if not data:
            self.report({'WARNING'}, "No shape keys to store")
            return {'CANCELLED'}

        target_path = bpy.utils.user_resource('SCRIPTS', path=f"presets/{self.preset_subdir}", create=True)
        filename = self.as_filename(self.name.strip()) + ".py"
        filepath = os.path.join(target_path, filename)

        # Generate standard executable python preset script
        lines = [
            "import bpy",
            "obj = bpy.context.active_object",
            "if obj and obj.type == 'MESH' and obj.data and obj.data.shape_keys:",
            "    kb = obj.data.shape_keys.key_blocks",
            "    wm = bpy.context.window_manager",
            "    # Save old values snapshot before applying preset",
            f"    wm.shapekey_transfer_settings.old_values_json = {repr(json.dumps(get_shapekey_dict(obj)))}",
            "    wm.shapekey_transfer_settings.has_backup = True",
        ]

        for k, v in data.items():
            lines.append(f"    if {repr(k)} in kb: kb[{repr(k)}].value = {float(v)}")
        lines.append("    obj.data.update()\n")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        self.report({'INFO'}, f"Preset '{self.name}' saved")
        return {'FINISHED'}


# -------------------------------------------------------------------
# Clipboard & Restore Operators
# -------------------------------------------------------------------

class MESH_OT_copy_shapekey_values(bpy.types.Operator):
    """Copy all shape key values to system clipboard"""
    bl_idname = "mesh.copy_shapekey_values"
    bl_label = "Copy Values"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH' and obj.data and obj.data.shape_keys)

    def execute(self, context):
        obj = context.active_object
        data = get_shapekey_dict(obj)
        if not data:
            self.report({'WARNING'}, "No non-basis shape keys found")
            return {'CANCELLED'}

        context.window_manager.clipboard = json.dumps(data, indent=2)
        self.report({'INFO'}, f"Copied {len(data)} shape key values")
        return {'FINISHED'}


class MESH_OT_paste_shapekey_values(bpy.types.Operator):
    """Paste shape key values from clipboard and preserve old values"""
    bl_idname = "mesh.paste_shapekey_values"
    bl_label = "Paste Values"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH' and obj.data and obj.data.shape_keys)

    def execute(self, context):
        obj = context.active_object
        clipboard = context.window_manager.clipboard.strip()

        try:
            target_data = json.loads(clipboard)
            if not isinstance(target_data, dict):
                raise ValueError
        except Exception:
            self.report({'ERROR'}, "Clipboard does not contain valid shape key JSON")
            return {'CANCELLED'}

        # Snapshot old values
        settings = context.window_manager.shapekey_transfer_settings
        settings.old_values_json = json.dumps(get_shapekey_dict(obj))
        settings.has_backup = True

        applied = apply_shapekey_dict(obj, target_data)
        self.report({'INFO'}, f"Pasted {applied} values (Old values preserved)")
        return {'FINISHED'}


class MESH_OT_restore_old_shapekey_values(bpy.types.Operator):
    """Restore the shape key values saved prior to the last paste or preset load"""
    bl_idname = "mesh.restore_old_shapekey_values"
    bl_label = "Restore Old Values"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.window_manager, "shapekey_transfer_settings", None)
        obj = context.active_object
        return bool(
            settings and settings.has_backup and 
            obj and obj.type == 'MESH' and obj.data and obj.data.shape_keys
        )

    def execute(self, context):
        obj = context.active_object
        settings = context.window_manager.shapekey_transfer_settings

        try:
            backup_dict = json.loads(settings.old_values_json)
        except Exception:
            self.report({'ERROR'}, "Failed to read backup state")
            return {'CANCELLED'}

        applied = apply_shapekey_dict(obj, backup_dict)
        self.report({'INFO'}, f"Restored {applied} old shape key values")
        return {'FINISHED'}


# -------------------------------------------------------------------
# Panels
# -------------------------------------------------------------------

class VIEW3D_PT_shapekey_presets_panel(PresetPanel, bpy.types.Panel):
    """Header preset bar mimicking standard Blender layout"""
    bl_label = "Shape Key Presets"
    preset_subdir = PRESET_SUBDIR
    preset_operator = "script.execute_preset"
    preset_add_operator = "mesh.shapekey_preset_add"


class VIEW3D_PT_shapekey_transfer(bpy.types.Panel):
    bl_label = "Shape Key Transfer"
    bl_idname = "VIEW3D_PT_shapekey_transfer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "ShapeKeys"

    def draw(self, context):
        layout = context.layout
        obj = context.active_object
        settings = context.window_manager.shapekey_transfer_settings

        # Native Preset Header (Menu + and - buttons)
        row = layout.row(align=True)
        row.menu("MESH_MT_shapekey_presets", text=bpy.types.MESH_MT_shapekey_presets.bl_label)
        row.operator("mesh.shapekey_preset_add", text="", icon='ADD')
        row.operator("mesh.shapekey_preset_add", text="", icon='REMOVE').remove_active = True

        layout.separator()

        if not obj or obj.type != 'MESH':
            layout.label(text="Select a Mesh Object", icon='INFO')
            return
        if not obj.data or not obj.data.shape_keys:
            layout.label(text="No Shape Keys found", icon='SHAPEKEY_DATA')
            return

        # Clipboard Operations
        box = layout.box()
        box.label(text="Clipboard Transfer", icon='COPYDOWN')
        col = box.column(align=True)
        row_c = col.row(align=True)
        row_c.operator("mesh.copy_shapekey_values", icon='COPY_ID')
        row_c.operator("mesh.paste_shapekey_values", icon='PASTEDOWN')

        # Snapshot / Old value rollback
        row_undo = col.row(align=True)
        row_undo.enabled = settings.has_backup
        row_undo.operator("mesh.restore_old_shapekey_values", icon='BACK')


# -------------------------------------------------------------------
# Registration
# -------------------------------------------------------------------

classes = (
    ShapeKeyTransferSettings,
    MESH_MT_shapekey_presets,
    MESH_OT_add_shapekey_preset,
    MESH_OT_copy_shapekey_values,
    MESH_OT_paste_shapekey_values,
    MESH_OT_restore_old_shapekey_values,
    VIEW3D_PT_shapekey_presets_panel,
    VIEW3D_PT_shapekey_transfer,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.shapekey_transfer_settings = PointerProperty(type=ShapeKeyTransferSettings)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.shapekey_transfer_settings


if __name__ == "__main__":
    register()
