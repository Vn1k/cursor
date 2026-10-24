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
  return ui.row({ key = `pv_{row.backend}_{row.name}`, gap = 8, align = "center", paddingH = 4 }, {
    row.protected and ui.glyph({ name = "alert-triangle", size = 14, color = "error" })
      or ui.box({ width = 14, height = 0 }),
    ui.label({ text = name, fontSize = 12, maxLines = 1, flexGrow = 1,
               color = row.protected and "error" or (row.kind == "picked" and "on_surface" or "on_surface_variant") }),
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

local function warning(P)
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
  -- A risky removal takes a second, explicit click that says it again.
  local count = P ~= nil and P.count or 0
  local armed = S.armed and P ~= nil and P.risky
  table.insert(footer, ui.row({ key = `actions_{armed and "armed" or "idle"}`, gap = 8, align = "center" }, {
    ui.label({ text = P ~= nil and fmt("frees", { size = size(P.freed) ~= "" and size(P.freed) or "0 B" }) or "",
               fontSize = 12, color = "on_surface_variant", flexGrow = 1 }),
    ui.button({ key = "back", text = tr("back"), variant = "ghost", controlSize = "sm",
                onClick = function() A.back() end }),
    ui.button({ key = "remove", variant = "destructive", enabled = P ~= nil and not S.busy,
                text = fmt(armed and "remove_confirm" or (count == 1 and "remove_one" or "remove_many"), { n = count }),
                onClick = function()
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
