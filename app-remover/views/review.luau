--!nonstrict
-- Step two: everything that will go, before any password is asked. dnf's own
-- answer, split into what you picked and what comes along with it.

local shared = require("../shared.luau")
local tr, fmt, size = shared.tr, shared.fmt, shared.size

local M = {}

local STEPS = { "step_pick", "step_review", "step_remove", "step_clean" }

-- The flow really is a sequence, so it is shown as one; past steps read as
-- done, the rest stay quiet.
function M.steps(current)
  local parts = {}
  for i, key in ipairs(STEPS) do
    if i > 1 then
      table.insert(parts, ui.glyph({ name = "chevron-right", size = 12, color = "outline" }))
    end
    table.insert(parts, ui.label({
      text = tr(key), fontSize = 12, fontWeight = i == current and "semibold" or "normal",
      color = i == current and "on_surface" or (i < current and "primary" or "on_surface_variant"),
    }))
  end
  return ui.row({ key = `steps_{current}`, gap = 6, align = "center" }, parts)
end

local function packageRow(row)
  local name = row.label ~= row.name and `{row.label}  ({row.name})` or row.name
  local locked = (row.locked or "") ~= ""
  local mark = ui.box({ width = 14, height = 0 })
  if locked then
    mark = ui.glyph({ name = "lock", size = 14, color = "error" })
  elseif row.protected then
    mark = ui.glyph({ name = "alert-triangle", size = 14, color = "error" })
  end
  return ui.row({ key = `pv_{row.backend}_{row.name}`, gap = 8, align = "center", paddingH = 4 }, {
    mark,
    ui.label({ text = name, fontSize = 12, maxLines = 1, flexGrow = 1,
               color = (locked or row.protected) and "error"
                 or (row.kind == "picked" and "on_surface" or "on_surface_variant") }),
    ui.label({ text = size(row.size), fontSize = 11, color = "on_surface_variant", width = 72, textAlign = "end" }),
  })
end

local function group(rows, kind, headingKey, hintKey)
  local members = {}
  for _, row in ipairs(rows) do
    if row.kind == kind then
      table.insert(members, packageRow(row))
    end
  end
  if #members == 0 then
    return ui.box({ height = 0 })
  end
  local head = {
    ui.label({ text = fmt(headingKey, { n = #members }), fontSize = 13, fontWeight = "semibold" }),
  }
  if hintKey ~= nil then
    table.insert(head, ui.label({ text = tr(hintKey), fontSize = 11, color = "on_surface_variant" }))
  end
  return ui.column({ key = `group_{kind}`, gap = 4, align = "stretch" }, {
    ui.column({ gap = 1 }, head),
    ui.column({ gap = 2, align = "stretch" }, members),
  })
end

-- The guardrail said no. One banner says what and why, and replaces the
-- warning: a block and a "careful" stacked up read as the same thing.
local function blockedBanner(P)
  local lines = {}
  for _, b in ipairs(P.blocked or {}) do
    local reason = tr(`lock_{b.reason}`)
    table.insert(lines, fmt(b.kind == "picked" and "blocked_picked" or "blocked_dependent",
                            { name = b.label or b.name, reason = reason }))
  end
  return ui.row({ key = "blocked", gap = 10, align = "start", padding = 10, radius = 8, border = "error",
                  borderWidth = 1 }, {
    ui.glyph({ name = "lock", size = 18, color = "error" }),
    ui.column({ flexGrow = 1, gap = 4 }, {
      ui.label({ text = tr("blocked_heading"), fontSize = 13, fontWeight = "semibold", color = "error" }),
      ui.label({ text = table.concat(lines, "\n"), fontSize = 12, color = "error" }),
      ui.label({ text = tr("blocked_hint"), fontSize = 11, color = "on_surface_variant" }),
    }),
  })
end

local function warning(P)
  if #(P.blocked or {}) > 0 then
    return blockedBanner(P)
  end
  if not P.risky then
    return ui.box({ height = 0 })
  end
  local lines = {}
  if #P.protected > 0 then
    table.insert(lines, fmt("risk_protected", { names = table.concat(P.protected, ", ") }))
  end
  if P.big then
    table.insert(lines, fmt("risk_big", { n = P.weighty, limit = P.limit }))
  end
  return ui.row({ key = "risk", gap = 10, align = "start", padding = 10, radius = 8, border = "error",
                  borderWidth = 1 }, {
    ui.glyph({ name = "alert-triangle", size = 18, color = "error" }),
    ui.label({ text = table.concat(lines, "\n"), fontSize = 12, color = "error", flexGrow = 1 }),
  })
end

-- Launchers no package owns: nothing is uninstalled, the launcher goes to
-- the Trash, and under each one the app files the engine found, off until
-- selected. "Not found" is said too, so an empty row is never a mystery.
local function launcherGroup(S, A, P)
  local members = {}
  for _, row in ipairs(P.rows) do
    if row.kind == "launcher" then
      local entry = { ui.label({ text = row.label, fontSize = 12, maxLines = 1 }) }
      local f = (row.files or {})[1]
      if f ~= nil then
        local on = S.withFiles[row.key] == true
        table.insert(entry, ui.row({ key = `files_{row.key}_{on and "on" or "off"}`, gap = 8, align = "center",
                                     paddingH = 6, paddingV = 4, radius = 6,
                                     onClick = function() A.toggleFiles(row.key) end }, {
          shared.checkbox(on),
          ui.label({ text = fmt("also_files", { path = shared.home(f.path) }), fontSize = 11, maxLines = 1,
                     flexGrow = 1, color = on and "on_surface" or "on_surface_variant" }),
          ui.label({ text = size(f.size), fontSize = 11, color = "on_surface_variant" }),
        }))
      else
        table.insert(entry, ui.label({ text = tr(row.gone and "app_gone" or "no_files"), fontSize = 11,
                                       color = "on_surface_variant" }))
      end
      table.insert(members, ui.column({ key = `launcher_{row.key}`, gap = 2, paddingH = 22, align = "stretch" }, entry))
    end
  end
  if #members == 0 then
    return ui.box({ height = 0 })
  end
  return ui.column({ key = "group_launcher", gap = 6, align = "stretch" }, {
    ui.column({ gap = 1 }, {
      ui.label({ text = fmt("launchers_heading", { n = #members }), fontSize = 13, fontWeight = "semibold" }),
      ui.label({ text = tr("launchers_hint"), fontSize = 11, color = "on_surface_variant" }),
    }),
    ui.column({ gap = 6, align = "stretch" }, members),
  })
end

-- What goes back to you: the packages, plus the app files selected so far.
local function freed(S, P)
  local bytes = P.freed or 0
  for _, row in ipairs(P.rows) do
    local f = (row.files or {})[1]
    if row.kind == "launcher" and f ~= nil and S.withFiles[row.key] then
      bytes += f.size or 0
    end
  end
  return bytes
end

-- "12 packages and 2 launchers": the button says exactly what goes.
local function what(P)
  local packages, launchers = 0, 0
  for _, row in ipairs(P.rows) do
    if row.kind == "launcher" then
      launchers += 1
    else
      packages += 1
    end
  end
  local parts = {}
  if packages > 0 then
    table.insert(parts, fmt(packages == 1 and "n_packages_one" or "n_packages_many", { n = packages }))
  end
  if launchers > 0 then
    table.insert(parts, fmt(launchers == 1 and "n_launchers_one" or "n_launchers_many", { n = launchers }))
  end
  return table.concat(parts, tr("and"))
end

local function hasFlatpak(P)
  for _, row in ipairs(P.rows) do
    if row.backend == "flatpak" then
      return true
    end
  end
  return false
end

function M.render(S, A)
  local P = S.preview
  local body
  if P == nil then
    body = { ui.label({ key = "resolving", text = tr("resolving"), fontSize = 12, color = "on_surface_variant" }) }
  else
    body = {
      group(P.rows, "picked", "picked_heading", nil),
      group(P.rows, "dependent", "dependent_heading", "dependent_hint"),
      group(P.rows, "unused", "unused_heading", "unused_hint"),
      launcherGroup(S, A, P),
    }
  end

  local footer = {}
  if P ~= nil and hasFlatpak(P) then
    table.insert(footer, ui.row({ key = `delete_data_{S.deleteData and "on" or "off"}`, gap = 10, align = "center",
                                  paddingH = 8, paddingV = 6, radius = 8,
                                  onClick = function() A.setDeleteData(not S.deleteData) end }, {
      shared.checkbox(S.deleteData),
      ui.column({ flexGrow = 1, gap = 1 }, {
        ui.label({ text = tr("delete_data"), fontSize = 12 }),
        ui.label({ text = tr("delete_data_hint"), fontSize = 11, color = "on_surface_variant" }),
      }),
    }))
  end
  -- A risky removal takes a second, explicit click that says it again. A
  -- blocked one has no Remove at all, only the way back.
  local isBlocked = P ~= nil and #(P.blocked or {}) > 0
  local armed = S.armed and P ~= nil and P.risky and not isBlocked
  table.insert(footer, ui.row({ key = `actions_{armed and "armed" or "idle"}`, gap = 8, align = "center" }, {
    ui.label({ text = P ~= nil and fmt("frees", { size = size(freed(S, P)) ~= "" and size(freed(S, P)) or "0 B" }) or "",
               fontSize = 12, color = "on_surface_variant", flexGrow = 1 }),
    ui.button({ key = "back", text = tr("back"), variant = "ghost", controlSize = "sm",
                onClick = function() A.back() end }),
    ui.button({ key = `remove_{isBlocked and "blocked" or "open"}`, variant = "destructive",
                enabled = P ~= nil and not S.busy and not isBlocked,
                text = isBlocked and tr("cant_remove")
                  or fmt(armed and "remove_confirm_what" or "remove_what", { what = P ~= nil and what(P) or "" }),
                onClick = function()
                  if isBlocked then
                    return
                  end
                  if P ~= nil and P.risky and not S.armed then
                    A.arm()
                  else
                    A.remove()
                  end
                end }),
  }))

  return ui.column({ key = "view_review", flexGrow = 1, gap = 12, align = "stretch" }, {
    M.steps(2),
    P ~= nil and warning(P) or ui.box({ height = 0 }),
    ui.scroll({ key = `review_rows_{P ~= nil and "ready" or "wait"}`, flexGrow = 1, gap = 14, align = "stretch" }, body),
    ui.separator({}),
    ui.column({ gap = 10, align = "stretch" }, footer),
  })
end

return M
