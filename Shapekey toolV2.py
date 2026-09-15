bl_info = {
    "name": "Shape Key Value Transfer (File Presets)",
    "author": "Dinesh",
    "version": (1, 4, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > ShapeKeys",
    "description": "Copy/paste shape keys across files with in-blend presets and undo restore",
    "category": "Mesh",
}

import bpy
import json
from bpy.props import StringProperty, BoolProperty, CollectionProperty, IntProperty


# -------------------------------------------------------------------
# Helper Utilities
# -------------------------------------------------------------------

def get_shapekey_dict(obj):
    """Extracts non-basis shape key name:value mappings."""
    if not obj or obj.type != 'MESH' or not obj.data or not obj.data.shape_keys:
        return {}
    key_blocks = obj.data.shape_keys.key_blocks
    ref_key = obj.data.shape_keys.reference_key
    return {kb.name: float(kb.value) for kb in key_blocks if kb != ref_key}


def apply_shapekey_dict(obj, data_dict):
    """Applies values to keys that exist on target mesh."""
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
# Data Storage in Scene (Saves with .blend file)
# -------------------------------------------------------------------

class ShapeKeyFilePresetItem(bpy.types.PropertyGroup):
    name: StringProperty(name="Preset Name", default="Preset")
    data_json: StringProperty(name="Data JSON", default="{}")


# -------------------------------------------------------------------
# Operators
# -------------------------------------------------------------------

class MESH_OT_add_file_preset(bpy.types.Operator):
    """Save active mesh shape key values as a preset in this blend file"""
    bl_idname = "mesh.add_shapekey_file_preset"
    bl_label = "Add Blend File Preset"
    bl_property = "preset_name"

    preset_name: StringProperty(
        name="Preset Name",
        description="Name of the preset to store in this file",
        default="New_Preset"
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or not obj.data or not obj.data.shape_keys:
            self.report({'WARNING'}, "Active object has no shape keys")
            return {'CANCELLED'}

        name = self.preset_name.strip()
        if not name:
            self.report({'ERROR'}, "Preset name cannot be empty")
            return {'CANCELLED'}

        data = get_shapekey_dict(obj)
        presets = context.scene.shapekey_file_presets

        # Update if name already exists, otherwise add new
        item = presets.get(name)
        if not item:
            item = presets.add()
            item.name = name

        item.data_json = json.dumps(data)
        context.scene.shapekey_active_preset_index = len(presets) - 1

        self.report({'INFO'}, f"Saved preset '{name}' to this .blend file")
        return {'FINISHED'}


class MESH_OT_remove_file_preset(bpy.types.Operator):
    """Remove active preset from this blend file"""
    bl_idname = "mesh.remove_shapekey_file_preset"
    bl_label = "Remove Preset"

    @classmethod
    def poll(cls, context):
        presets = getattr(context.scene, "shapekey_file_presets", None)
        return bool(presets and len(presets) > 0)

    def execute(self, context):
        scene = context.scene
        idx = scene.shapekey_active_preset_index
        presets = scene.shapekey_file_presets

        if 0 <= idx < len(presets):
            removed_name = presets[idx].name
            presets.remove(idx)
            scene.shapekey_active_preset_index = max(0, idx - 1)
            self.report({'INFO'}, f"Removed preset '{removed_name}'")
            return {'FINISHED'}

        return {'CANCELLED'}


class MESH_OT_apply_file_preset(bpy.types.Operator):
    """Apply the selected blend file preset to the active mesh"""
    bl_idname = "mesh.apply_shapekey_file_preset"
    bl_label = "Apply Selected Preset"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        presets = getattr(context.scene, "shapekey_file_presets", None)
        return bool(
            presets and len(presets) > 0 and
            obj and obj.type == 'MESH' and obj.data and obj.data.shape_keys
        )

    def execute(self, context):
        scene = context.scene
        idx = scene.shapekey_active_preset_index
        presets = scene.shapekey_file_presets

        if not (0 <= idx < len(presets)):
            self.report({'WARNING'}, "No preset selected")
            return {'CANCELLED'}

        obj = context.active_object
        item = presets[idx]

        try:
            data = json.loads(item.data_json)
        except Exception:
            self.report({'ERROR'}, "Corrupted preset data")
            return {'CANCELLED'}

        # Snapshot current values into undo buffer before applying
        context.window_manager.shapekey_backup_json = json.dumps(get_shapekey_dict(obj))
        context.window_manager.shapekey_has_backup = True

        applied = apply_shapekey_dict(obj, data)
        self.report({'INFO'}, f"Applied '{item.name}' ({applied} keys updated)")
        return {'FINISHED'}


class MESH_OT_copy_shapekey_values(bpy.types.Operator):
    """Copy all shape key values to system clipboard for another blend file"""
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
            self.report({'WARNING'}, "No non-basis shape keys found on mesh")
            return {'CANCELLED'}

        context.window_manager.clipboard = json.dumps(data, indent=2)
        self.report({'INFO'}, f"Copied {len(data)} shape key values to clipboard")
        return {'FINISHED'}


class MESH_OT_paste_shapekey_values(bpy.types.Operator):
    """Paste shape key values from clipboard and save old values"""
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

        if not clipboard:
            self.report({'ERROR'}, "Clipboard is empty")
            return {'CANCELLED'}

        try:
            target_data = json.loads(clipboard)
            if not isinstance(target_data, dict):
                raise ValueError
        except Exception:
            self.report({'ERROR'}, "Clipboard does not contain valid shape key JSON")
            return {'CANCELLED'}

        # Snapshot values before paste
        context.window_manager.shapekey_backup_json = json.dumps(get_shapekey_dict(obj))
        context.window_manager.shapekey_has_backup = True

        applied = apply_shapekey_dict(obj, target_data)
        self.report({'INFO'}, f"Pasted {applied} shape key values (Old values preserved)")
        return {'FINISHED'}


class MESH_OT_restore_old_shapekey_values(bpy.types.Operator):
    """Restore the previous values prior to the last paste or preset application"""
    bl_idname = "mesh.restore_old_shapekey_values"
    bl_label = "Restore Old Values"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        wm = context.window_manager
        obj = context.active_object
        return bool(
            getattr(wm, "shapekey_has_backup", False) and
            obj and obj.type == 'MESH' and obj.data and obj.data.shape_keys
        )

    def execute(self, context):
        obj = context.active_object
        wm = context.window_manager

        try:
            backup_dict = json.loads(wm.shapekey_backup_json)
        except Exception:
            self.report({'ERROR'}, "No valid backup state found")
            return {'CANCELLED'}

        applied = apply_shapekey_dict(obj, backup_dict)
        self.report({'INFO'}, f"Restored {applied} old shape key values")
        return {'FINISHED'}


# -------------------------------------------------------------------
# UI Panel (3D Viewport Sidebar Only)
# -------------------------------------------------------------------

class VIEW3D_PT_shapekey_file_presets(bpy.types.Panel):
    bl_label = "Shape Key Presets"
    bl_idname = "VIEW3D_PT_shapekey_file_presets"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "ShapeKeys"

    def draw(self, context):
        layout = context.layout
        scene = context.scene
        wm = context.window_manager
        obj = context.active_object

        # Presets stored in this blend file
        box_presets = layout.box()
        box_presets.label(text="In-Blend Presets", icon='PRESET')

        presets = scene.shapekey_file_presets
        has_presets = len(presets) > 0

        # Header with Add / Remove operators
        row = box_presets.row(align=True)
        if has_presets:
            row.template_list(
                "UI_UL_list", 
                "shapekey_presets_list", 
                scene, 
                "shapekey_file_presets", 
                scene, 
                "shapekey_active_preset_index",
                rows=2
            )
        else:
            row.label(text="No presets saved in this file", icon='INFO')

        sub_col = row.column(align=True)
        sub_col.operator("mesh.add_shapekey_file_preset", text="", icon='ADD')
        sub_col.operator("mesh.remove_shapekey_file_preset", text="", icon='REMOVE')

        # Apply Preset button
        row_apply = box_presets.row()
        row_apply.enabled = has_presets and bool(obj and obj.type == 'MESH' and obj.data.shape_keys)
        row_apply.operator("mesh.apply_shapekey_file_preset", text="Apply Selected Preset", icon='CHECKMARK')

        layout.separator()

        # Cross-file clipboard operations
        box_clip = layout.box()
        box_clip.label(text="Inter-File Clipboard", icon='COPYDOWN')
        col_c = box_clip.column(align=True)

        row_btns = col_c.row(align=True)
        row_btns.operator("mesh.copy_shapekey_values", text="Copy Values", icon='COPY_ID')
        row_btns.operator("mesh.paste_shapekey_values", text="Paste Values", icon='PASTEDOWN')

        # Restore snapshot
        col_c.separator(factor=0.5)
        row_undo = col_c.row(align=True)
        row_undo.enabled = wm.shapekey_has_backup
        row_undo.operator("mesh.restore_old_shapekey_values", text="Restore Old Values", icon='BACK')


# -------------------------------------------------------------------
# Registration
# -------------------------------------------------------------------

classes = (
    ShapeKeyFilePresetItem,
    MESH_OT_add_file_preset,
    MESH_OT_remove_file_preset,
    MESH_OT_apply_file_preset,
    MESH_OT_copy_shapekey_values,
    MESH_OT_paste_shapekey_values,
    MESH_OT_restore_old_shapekey_values,
    VIEW3D_PT_shapekey_file_presets,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    # Saved with the .blend file
    bpy.types.Scene.shapekey_file_presets = CollectionProperty(type=ShapeKeyFilePresetItem)
    bpy.types.Scene.shapekey_active_preset_index = IntProperty(name="Active Preset Index", default=0)

    # Temporary session undo buffer
    bpy.types.WindowManager.shapekey_backup_json = StringProperty(default="{}")
    bpy.types.WindowManager.shapekey_has_backup = BoolProperty(default=False)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.shapekey_file_presets
    del bpy.types.Scene.shapekey_active_preset_index
    del bpy.types.WindowManager.shapekey_backup_json
    del bpy.types.WindowManager.shapekey_has_backup


if __name__ == "__main__":
    register()
