--!nonstrict
-- Step one: find what to remove. The ledger at the foot is the one loud thing
-- on screen - it grows with every pick, so what will go is never a surprise.

local shared = require("../shared.luau")
local tr, fmt, size = shared.tr, shared.fmt, shared.size

local M = {}

-- A table: every row and the header share these widths and the one gap, so
-- Type and Size line up down the page whatever the name or label length.
local GAP = 10
local CHECK = shared.CHECK
local ICON = 28
local TYPE = 90
local SIZE = 80
local SOURCES = { "all", "rpm", "flatpak" }

local function matches(S, item, showUnpackaged)
  local source = SOURCES[S.filter + 1]
  if source ~= "all" and item.backend ~= source then
    return false
  end
  if item.backend == "unpackaged" and not showUnpackaged then
    return false
  end
  local q = string.lower(shared.trim(S.query))
  if q == "" then
    return true
  end
  return string.find(string.lower(item.name), q, 1, true) ~= nil
    or string.find(string.lower(item.pkg or ""), q, 1, true) ~= nil
end

function M.visible(S)
  local out = {}
  local showUnpackaged = shared.setting("show_unpackaged", true) ~= false
  for _, item in ipairs(S.items) do
    if matches(S, item, showUnpackaged) then
      table.insert(out, item)
    end
  end
  -- The engine already sends them by name; only another order costs a sort.
  if S.sort.by == "size" then
    table.sort(out, function(a, b)
      if (a.size or 0) == (b.size or 0) then
        return a.name < b.name
      end
      return S.sort.desc == ((a.size or 0) > (b.size or 0))
    end)
  elseif S.sort.desc then
    local n = #out
    for i = 1, math.floor(n / 2) do
      out[i], out[n - i + 1] = out[n - i + 1], out[i]
    end
  end
  return out
end

-- Looked up once per render, not per row: every host call counts against
-- the callback's CPU budget, and a page has forty rows.
local function labels()
  return { unpackaged = tr("not_packaged"), flatpak = tr("source_flatpak"), rpm = tr("source_rpm"),
           preinstalled = tr("preinstalled"),
           lock = { dnf = tr("lock_dnf"), core = tr("lock_core"), session = tr("lock_session"),
                    never = tr("lock_never") } }
end

-- What the row is underneath its name: why it cannot go, or that it came with
-- the system, then the package when the name does not give it away, and how
-- many launchers go with it.
local function detail(item, L)
  local bits = {}
  if (item.locked or "") ~= "" then
    table.insert(bits, L.lock[item.locked] or item.locked)
  elseif item.preinstalled then
    table.insert(bits, L.preinstalled)
  end
  if (item.pkg or "") ~= "" and string.lower(item.pkg) ~= string.lower(item.name) then
    table.insert(bits, item.pkg)
  end
  if (item.launchers or 0) > 1 then
    table.insert(bits, fmt("launchers", { n = item.launchers }))
  end
  return table.concat(bits, ", ")
end

local function lead(item, path)
  if path ~= nil then
    return ui.image({ key = `icon_{item.key}`, path = path, width = ICON, height = ICON, fit = "contain" })
  end
  return ui.column({ width = ICON, height = ICON, align = "center", justify = "center" }, {
    ui.glyph({ name = item.backend == "unpackaged" and "file-unknown" or "package", size = 18,
               color = "on_surface_variant" }),
  })
end

-- A fixed-width table cell. The width goes on a row around the label: a
-- label's own width is ignored and it shrinks to its text, which is what left
-- "rpm" and "No package" starting at different places.
local function cell(width, alignEnd, child)
  return ui.row({ width = width, minWidth = width, justify = alignEnd and "end" or "start", align = "center" },
                { child })
end

local function row(S, A, L, item)
  local picked = S.picked[item.key] ~= nil
  local locked = (item.locked or "") ~= ""
  local removable = item.backend ~= "unpackaged" and not locked
  local sub = detail(item, L)
  -- The icon state is in the key too. Swapping the placeholder for the image
  -- inside a kept row left the row at its old, content-sized width, and every
  -- name truncated to three letters.
  local icon = A.icon(item)
  return ui.row({
    key = `row_{item.key}_{picked and "on" or "off"}_{icon ~= nil and "icon" or "glyph"}`, gap = GAP, align = "center",
    paddingH = 8, paddingV = 6, radius = 8, fill = picked and "surface_variant" or nil,
    onClick = (removable and not S.busy) and function() A.toggle(item) end or nil,
  }, {
    -- A lock, not a dimmed box: it says this one is held back on purpose,
    -- not that something is broken.
    locked and shared.lockbox() or shared.checkbox(picked, removable),
    lead(item, icon),
    ui.column({ flexGrow = 1, gap = 1, align = "stretch" }, {
      ui.label({ text = item.name, fontSize = 13, fontWeight = "medium", maxLines = 1,
                 color = removable and "on_surface" or "on_surface_variant" }),
      sub ~= "" and ui.label({ text = sub, fontSize = 11, maxLines = 1, color = "on_surface_variant" })
        or ui.box({ height = 0 }),
    }),
    cell(TYPE, false, ui.label({ text = L[item.backend] or "", fontSize = 12, maxLines = 1,
                                 color = "on_surface_variant" })),
    cell(SIZE, true, ui.label({ text = size(item.size), fontSize = 12, color = "on_surface_variant" })),
  })
end

-- A column heading that sorts. The arrow only shows on the column in use, and
-- which way it points is the order you see.
local function heading(S, A, by, labelKey, width, alignEnd)
  local active = S.sort.by == by
  local parts = {
    ui.label({ text = tr(labelKey), fontSize = 11, fontWeight = "semibold",
               color = active and "on_surface" or "on_surface_variant" }),
  }
  if active then
    table.insert(parts, ui.glyph({ name = S.sort.desc and "arrow-down" or "arrow-up", size = 12,
                                   color = "on_surface" }))
  end
  return ui.row({ key = `head_{by}_{active and (S.sort.desc and "desc" or "asc") or "off"}`, gap = 4,
                  align = "center", justify = alignEnd and "end" or "start", width = width, minWidth = width,
                  flexGrow = width == nil and 1 or nil, onClick = function() A.sortBy(by) end }, parts)
end

local function header(S, A)
  return ui.row({ key = "table_head", gap = GAP, align = "center", paddingH = 8 }, {
    ui.box({ width = CHECK, height = 0 }),
    ui.box({ width = ICON, height = 0 }),
    heading(S, A, "name", "col_name", nil, false),
    cell(TYPE, false, ui.label({ text = tr("col_type"), fontSize = 11, fontWeight = "semibold",
                                 color = "on_surface_variant" })),
    heading(S, A, "size", "col_size", SIZE, true),
  })
end

local function emptyText(S)
  if S.busy then
    return tr("loading")
  end
  if shared.trim(S.query) ~= "" then
    return fmt(S.mode == "apps" and "no_match_apps" or "no_match", { query = shared.trim(S.query) })
  end
  return tr("nothing_listed")
end

-- A skipped backend is said once, plainly, instead of an empty source filter.
local function skipped(S)
  local lines = {}
  for _, name in ipairs({ "rpm", "flatpak" }) do
    local b = S.backends[name]
    if type(b) == "table" and b.skipped then
      table.insert(lines, ui.label({ text = tr(`skipped_{name}`), fontSize = 11, color = "on_surface_variant" }))
    end
  end
  if #lines == 0 then
    return ui.box({ height = 0 })
  end
  return ui.column({ key = "skipped", gap = 2 }, lines)
end

local function modeButton(S, A, id)
  return ui.button({ key = `mode_{id}_{S.mode == id and "on" or "off"}`, text = tr(`mode_{id}`),
                     variant = S.mode == id and "secondary" or "ghost", controlSize = "sm",
                     enabled = not S.busy, onClick = function() A.setMode(id) end })
end

-- The ledger: a rule in the accent colour, the running tally, and the one
-- primary action. Nothing picked, it says what to do instead.
local function ledger(S, A)
  local n, bytes = 0, 0
  for _, item in pairs(S.picked) do
    n += 1
    bytes += item.size or 0
  end
  local text = n == 0 and tr("ledger_empty")
    or fmt(n == 1 and "ledger_one" or "ledger_many", { n = n, size = size(bytes) ~= "" and size(bytes) or "0 B" })
  return ui.row({ key = `ledger_{n > 0 and "on" or "off"}`, gap = 12, align = "center" }, {
    ui.box({ width = 3, height = 28, radius = 2, fill = n > 0 and "primary" or "outline" }),
    ui.column({ flexGrow = 1, gap = 1 }, {
      ui.label({ text = text, fontSize = 14, fontWeight = n > 0 and "semibold" or "normal",
                 color = n > 0 and "on_surface" or "on_surface_variant", maxLines = 1 }),
      S.status ~= "" and ui.label({ text = S.status, fontSize = 11, color = S.statusTone, maxLines = 1 })
        or ui.box({ height = 0 }),
    }),
    n > 0 and ui.button({ key = "clear", text = tr("clear"), variant = "ghost", controlSize = "sm",
                          onClick = function() A.clear() end })
      or ui.box({ width = 0, height = 0 }),
    ui.button({ key = "review", text = tr("review"), variant = "primary", enabled = n > 0 and not S.busy,
                onClick = function() A.review() end }),
  })
end

function M.render(S, A)
  local visible = M.visible(S)
  local rows, L = {}, labels()
  for i, item in ipairs(visible) do
    if i > S.shown then
      table.insert(rows, ui.button({ key = `more_{S.shown}`, text = fmt("show_more", { n = #visible - S.shown }),
                                     variant = "ghost", controlSize = "sm", onClick = function() A.more() end }))
      break
    end
    table.insert(rows, row(S, A, L, item))
  end
  if #rows == 0 then
    rows = { ui.label({ key = "empty", text = emptyText(S), fontSize = 12, color = "on_surface_variant" }) }
  else
    -- The header is the scroll's first row, not a row above it: the scrollbar
    -- takes its gutter only when the list overflows, so a header outside sat
    -- off the columns by that much on long lists and not on short ones.
    table.insert(rows, 1, header(S, A))
    table.insert(rows, 2, ui.separator({ key = "head_rule" }))
  end
  return ui.column({ key = "view_list", flexGrow = 1, gap = 12, align = "stretch" }, {
    ui.row({ gap = 8, align = "center" }, {
      ui.input({ key = `query_{S.mode}`, placeholder = tr(S.mode == "apps" and "search_apps" or "search_packages"),
                 value = S.query, flexGrow = 1, onChange = function(v) A.setQuery(v) end }),
      ui.select({ key = "source", options = { tr("source_all"), tr("source_rpm"), tr("source_flatpak") },
                  selectedIndex = S.filter, width = 130, controlSize = "sm",
                  onChange = function(index) A.setFilter(tonumber(index) or 0) end }),
      modeButton(S, A, "apps"),
      modeButton(S, A, "packages"),
    }),
    skipped(S),
    ui.column({ flexGrow = 1, gap = 6, align = "stretch" }, {
      -- The key changes with what fills it: the host keeps a node's size, and a
      -- scroll first drawn around "Finding installed apps…" kept that width,
      -- squeezing every row that came after it.
      ui.scroll({ key = `list_{S.mode}_{#visible > 0 and "rows" or "empty"}`, flexGrow = 1, gap = 2,
                  align = "stretch" }, rows),
    }),
    ui.separator({}),
    ledger(S, A),
  })
end

return M
