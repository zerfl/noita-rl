-- Player LuaComponent (script_damage_received): appends "message=damage;" for wand_eval's
-- self-damage breakdown. Component scripts run in their own Lua state, hence the globals.
function damage_received(damage, message, entity_thats_responsible, is_fatal, projectile_thats_responsible)
  if damage > 0 then
    GlobalsSetValue("rlb_dmg_log", GlobalsGetValue("rlb_dmg_log", "") .. tostring(message) .. "=" .. damage .. ";")
  end
end
