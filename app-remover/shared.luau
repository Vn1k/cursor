--!nonstrict
-- Helpers every view needs: quoting, settings, sizes, and how to run the engine.

local M = {}

function M.shellQuote(s)
  return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end

function M.trim(s)
  return noctalia.string.trim(tostring(s or ""))
end

function M.tr(key)
  return noctalia.tr(key)
end

-- tr() with {name}-style holes filled in.
function M.fmt(key, values)
  local text = noctalia.tr(key)
  for name, value in pairs(values) do
    text = string.gsub(text, "{" .. name .. "}", (string.gsub(tostring(value), "%%", "%%%%")))
  end
  return text
end

function M.setting(key, default)
  local value = noctalia.getConfig(key)
  if value == nil then
    return default
  end
  return value
end

local function listSetting(key)
  local out = {}
  local list = noctalia.getConfig(key)
  if type(list) == "table" then
    for _, item in ipairs(list) do
      local s = M.trim(item)
      if s ~= "" then
        table.insert(out, s)
      end
    end
  end
  return out
end

-- Settings reach the engine as env vars, never flags, so a keybind can export
-- the same names. Only what is set is exported: unset leaves the engine's default.
function M.env()
  local vars = {}
  local protected = listSetting("protected_packages")
  if #protected > 0 then
    table.insert(vars, `APPMGR_PROTECTED={M.shellQuote(table.concat(protected, ":"))}`)
  end
  local dirs = {}
  for _, dir in ipairs(listSetting("leftover_dirs")) do
    table.insert(dirs, noctalia.expandPath(dir))
  end
  if #dirs > 0 then
    table.insert(vars, `APPMGR_LEFTOVER_DIRS={M.shellQuote(table.concat(dirs, ":"))}`)
  end
  local big = tonumber(M.setting("big_transaction", 0))
  if big ~= nil and big > 0 then
    table.insert(vars, `APPMGR_BIG_TRANSACTION={math.floor(big)}`)
  end
  local elevate = M.trim(M.setting("elevate_command", ""))
  if elevate ~= "" then
    table.insert(vars, `APPMGR_ELEVATE={M.shellQuote(elevate)}`)
  end
  return #vars > 0 and table.concat(vars, " ") .. " " or ""
end

-- The engine ships inside the plugin, so nothing has to be on PATH;
-- engine_path is only an override.
function M.engine()
  local override = M.trim(M.setting("engine_path", ""))
  if override ~= "" then
    return M.env() .. M.shellQuote(noctalia.expandPath(override))
  end
  return `{M.env()}python3 -B {M.shellQuote(noctalia.pluginDir() .. "/bin/appmgr.py")}`
end

-- Decimal units, the way dnf and flatpak print them.
function M.size(bytes)
  bytes = tonumber(bytes) or 0
  if bytes <= 0 then
    return ""
  end
  for _, unit in ipairs({ "B", "KB", "MB", "GB" }) do
    if bytes < 1000 or unit == "GB" then
      return unit == "B" and `{bytes} B` or string.format("%.1f %s", bytes, unit)
    end
    bytes /= 1000
  end
  return ""
end

-- A checkbox, not a toggle: a toggle means "this takes effect now", and in
-- this panel nothing does until the button at the bottom. The row around it
-- is the click target; the box only shows the state. Disabled shows a dimmed
-- empty box.
M.CHECK = 18

function M.checkbox(checked, enabled)
  return ui.glyph({ name = checked and "square-check-filled" or "square", size = M.CHECK,
                    color = checked and "primary" or (enabled ~= false and "on_surface_variant" or "outline") })
end

-- ~/.config/foo reads better than /home/name/.config/foo.
function M.home(path)
  local home = noctalia.expandPath("~")
  if string.sub(path, 1, #home + 1) == home .. "/" then
    return "~" .. string.sub(path, #home + 1)
  end
  return path
end

return M
