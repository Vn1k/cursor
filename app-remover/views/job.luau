--!nonstrict
-- Steps three and four: the removal as it runs, then what it left behind in
-- your home folder. Leftovers start unticked and only ever go to the Trash.

local shared = require("../shared.luau")
local review = require("./review.luau")
local tr, fmt, size = shared.tr, shared.fmt, shared.size

local M = {}

local LOG_SHOWN = 40

-- dnf's own output, for when something needs explaining. It fills the space
-- it is given: a fixed height was kept from the first, one-line render.
local function logView(lines)
  local shown = {}
  for i = math.max(1, #lines - LOG_SHOWN + 1), #lines do
    table.insert(shown, ui.label({ text = lines[i], fontSize = 11, maxLines = 1, color = "on_surface_variant" }))
  end
  if #shown == 0 then
    return ui.box({ height = 0 })
  end
  return ui.scroll({ key = "log", flexGrow = 1, gap = 1, align = "stretch", padding = 10, radius = 8,
                     fill = "surface_variant", stickToBottom = true }, shown)
end

local function detailsButton(S, A)
  return ui.button({ key = `log_{S.showLog and "open" or "shut"}`, variant = "ghost", controlSize = "sm",
                     text = tr(S.showLog and "hide_log" or "show_log"),
                     glyph = S.showLog and "chevron-up" or "chevron-down",
                     onClick = function() A.toggleLog() end })
end

-- What is happening, in words and a bar: dnf counts its steps ("[10/13]"),
-- and the engine passes the count and the package on. Without a count (the
-- password, flatpak) the line says what it is waiting for instead.
local function progressBlock(info)
  local p = info.progress
  if type(p) ~= "table" or (p.steps or 0) == 0 then
    return ui.box({ height = 0 })
  end
  return ui.column({ key = "progress", gap = 6, align = "stretch" }, {
    ui.progress({ progress = math.min(1, p.step / p.steps), height = 6, radius = 3 }),
    ui.label({ text = fmt("progress_step", { label = p.label or "", n = p.step, total = p.steps }),
               fontSize = 12, maxLines = 1, color = "on_surface_variant" }),
  })
end

local function running(S, A)
  local info = S.job.info or {}
  local started = #(info.log or {}) > 0
  return ui.column({ key = "view_running", flexGrow = 1, gap = 12, align = "stretch" }, {
    review.steps(3),
    ui.row({ gap = 10, align = "center" }, {
      ui.glyph({ name = started and "loader" or "lock", size = 18, color = "primary" }),
      ui.label({ text = tr(started and "removing" or "waiting_password"), fontSize = 14, fontWeight = "semibold",
                 flexGrow = 1 }),
      started and detailsButton(S, A) or ui.box({ width = 0, height = 0 }),
    }),
    started and progressBlock(info)
      or ui.label({ text = tr("waiting_password_hint"), fontSize = 12, color = "on_surface_variant" }),
    (started and S.showLog) and logView(info.log) or ui.spacer({ flexGrow = 1 }),
  })
end

-- Same verb as the button: "Remove 3 packages" ends as "Removed 3 packages".
local function headline(R)
  if R.cancelled and #(R.removed or {}) == 0 then
    return "x", "error", tr("cancelled")
  end
  if not R.ok and #(R.removed or {}) == 0 then
    -- dnf said no: name what stayed and point at dnf's reason below, rather
    -- than repeating the engine's summary of the same thing.
    local names = {}
    for _, f in ipairs(R.failed or {}) do
      table.insert(names, f.name)
    end
    if #names > 0 then
      return "circle-x", "error", fmt("failed_names", { names = table.concat(names, ", ") })
    end
    return "circle-x", "error", fmt("failed", { error = R.error or tr("error") })
  end
  local n = #(R.removed or {})
  local text = fmt(n == 1 and "removed_one" or "removed_many", { n = n })
  local failed = {}
  for _, f in ipairs(R.failed or {}) do
    table.insert(failed, f.name)
  end
  if #failed > 0 then
    text ..= "\n" .. fmt("not_removed", { names = table.concat(failed, ", ") })
  end
  return "circle-check", "primary", text
end

local function leftoverRow(S, A, dir)
  local ticked = S.toTrash[dir.path] == true
  return ui.row({ key = `lo_{dir.path}_{ticked and "on" or "off"}`, gap = 10, align = "center", paddingH = 8,
                  paddingV = 6, radius = 8, fill = ticked and "surface_variant" or nil,
                  onClick = not S.busy and function() A.toggleLeftover(dir.path) end or nil }, {
    shared.checkbox(ticked),
    ui.label({ text = shared.home(dir.path), fontSize = 12, maxLines = 1, flexGrow = 1 }),
    ui.label({ text = size(dir.size), fontSize = 11, color = "on_surface_variant", width = 72, textAlign = "end" }),
  })
end

local function finished(S, A)
  local R = S.result or {}
  local glyph, tone, text = headline(R)
  local dirs = R.leftovers or {}
  local chosen = 0
  for _, dir in ipairs(dirs) do
    if S.toTrash[dir.path] then
      chosen += 1
    end
  end

  local body = {}
  if #dirs > 0 then
    table.insert(body, ui.column({ gap = 1 }, {
      ui.label({ text = fmt("leftovers_heading", { n = #dirs }), fontSize = 13, fontWeight = "semibold" }),
      ui.label({ text = tr("leftovers_hint"), fontSize = 11, color = "on_surface_variant" }),
    }))
    local rows = {}
    for _, dir in ipairs(dirs) do
      table.insert(rows, leftoverRow(S, A, dir))
    end
    table.insert(body, ui.scroll({ key = "leftovers", flexGrow = 1, gap = 4, align = "stretch" }, rows))
  elseif not R.ok and not R.cancelled and #(S.log or {}) > 0 then
    -- A failure is explained by dnf's own words, so they show without asking.
    table.insert(body, logView(S.log))
  elseif #(R.removed or {}) > 0 then
    table.insert(body, ui.label({ text = tr("no_leftovers"), fontSize = 12, color = "on_surface_variant" }))
    table.insert(body, ui.spacer({ flexGrow = 1 }))
  else
    table.insert(body, ui.spacer({ flexGrow = 1 }))
  end

  local actions = { ui.label({ text = S.status, fontSize = 12, color = S.statusTone, maxLines = 1, flexGrow = 1 }) }
  if #dirs > 0 then
    table.insert(actions, ui.button({ key = "keep_all", text = tr("keep_all"), variant = "ghost", controlSize = "sm",
                                      enabled = not S.busy, onClick = function() A.done() end }))
    table.insert(actions, ui.button({ key = `trash_{chosen}`, variant = "destructive", enabled = chosen > 0 and not S.busy,
                                      text = fmt(chosen == 1 and "trash_one" or "trash_many", { n = chosen }),
                                      onClick = function() A.trash() end }))
  else
    table.insert(actions, ui.button({ key = "done", text = tr("done"), variant = "primary",
                                      onClick = function() A.done() end }))
  end

  return ui.column({ key = "view_finished", flexGrow = 1, gap = 12, align = "stretch" }, {
    review.steps(#dirs > 0 and 4 or 3),
    ui.row({ gap = 10, align = "start" }, {
      ui.glyph({ name = glyph, size = 18, color = tone }),
      ui.label({ text = text, fontSize = 14, fontWeight = "semibold", flexGrow = 1 }),
    }),
    ui.column({ flexGrow = 1, gap = 8, align = "stretch" }, body),
    ui.separator({}),
    ui.row({ gap = 8, align = "center" }, actions),
  })
end

function M.render(S, A)
  if S.result == nil then
    return running(S, A)
  end
  return finished(S, A)
end

return M
