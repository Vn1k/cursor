--!nonstrict
-- App remover panel.
--
-- bin/appmgr.py owns every lookup and every removal; this file shells out to
-- it and draws the JSON it prints. State lives in S, handlers in A; the views
-- under views/ only read S and call A.

local shared = require("./shared.luau")
local listView = require("./views/list.luau")
local reviewView = require("./views/review.luau")
local jobView = require("./views/job.luau")
local shellQuote, tr, fmt = shared.shellQuote, shared.tr, shared.fmt

local PANEL_ID = "vn1k/app-remover:manager"
local ENGINE_TIMEOUT_MS = 60000 -- runAsync's maximum; `remove` runs detached instead
-- Rows drawn at once; "Show more" adds the next page. Every callback gets
-- 25 ms of CPU, and building and reconciling the tree counts against it.
local PAGE = 40
local ICON_PX = 32

local render

-- ── state ───────────────────────────────────────────────────────────────────
-- Module globals outlive a close, so a reopen shows the last view at once.

local S = {
  view = "list",        -- "list" | "review" | "job"
  busy = false,
  status = "", statusTone = "on_surface_variant",
  mode = nil,           -- "apps" | "packages"; the default_view setting on first open
  filter = 0,           -- source select: 0 all, 1 rpm, 2 flatpak
  query = "",
  sort = { by = "name", desc = false },
  items = {}, backends = {},
  shown = PAGE,
  picked = {},          -- key -> item; survives a mode switch
  preview = nil,        -- the engine's preview answer
  armed = false,        -- a risky removal's first click
  deleteData = false,
  withFiles = {},       -- launcher key -> also trash its app files
  job = nil,            -- { info = state file contents } while a removal runs
  result = nil,         -- the removal's answer, until Done
  log = {},             -- dnf's output from the last removal
  showLog = false,      -- "Show details" while it runs
  toTrash = {},         -- leftover path -> ticked
}
local A = {}
local icons = {}        -- item key -> icon path, or false for none
local gen = 0           -- a list answer landing after a newer request is ignored

local function setStatus(text, tone)
  S.status = text
  S.statusTone = tone or "on_surface_variant"
end

local function fail(message)
  S.busy = false
  setStatus(message, "error")
  noctalia.notifyError(tr("title"), message)
  render()
end

-- ── engine ──────────────────────────────────────────────────────────────────

-- Every subcommand answers with one JSON object, so a decode failure means the
-- engine itself is missing or broken rather than the request being invalid.
local function call(args, done)
  if shared.trim(shared.setting("engine_path", "")) == "" and not noctalia.commandExists("python3") then
    done(nil, tr("engine_missing"))
    return
  end
  noctalia.runAsync(`{shared.engine()} {args}`, function(result)
    if result == nil then
      done(nil, tr("engine_missing"))
      return
    end
    if result.timedOut then
      done(nil, tr("engine_timeout"))
      return
    end
    local decoded = noctalia.json.decode(result.stdout or "")
    if type(decoded) ~= "table" then
      local stderr = shared.trim(result.stderr)
      done(nil, stderr ~= "" and stderr or tr("engine_missing"))
      return
    end
    if not decoded.ok then
      done(nil, tostring(decoded.error or tr("error")))
      return
    end
    done(decoded, nil)
  end, ENGINE_TIMEOUT_MS)
end

-- One fixed file: a removal that finished while the panel was closed, or
-- even across a plugin reload, is still found on the next open.
local function statePath()
  local dir = noctalia.pluginDataDir()
  return type(dir) == "string" and dir ~= "" and `{dir}/remove.json` or nil
end

local function pickedKeys()
  local keys = {}
  for key in pairs(S.picked) do
    table.insert(keys, key)
  end
  table.sort(keys)
  return keys
end

local function quoted(list)
  local out = {}
  for _, s in ipairs(list) do
    table.insert(out, shellQuote(s))
  end
  return table.concat(out, " ")
end

local function loadList()
  gen += 1
  local mine = gen
  S.busy = true
  render()
  call(S.mode == "packages" and "list --all" or "list", function(result, err)
    if mine ~= gen then
      return
    end
    if result == nil then
      fail(err)
      return
    end
    S.busy = false
    S.items, S.backends = result.items or {}, result.backends or {}
    -- A pick that is locked now (added to Never remove since) is dropped.
    for _, item in ipairs(S.items) do
      if (item.locked or "") ~= "" then
        S.picked[item.key] = nil
      end
    end
    render()
  end)
end

-- ── the removal, detached ───────────────────────────────────────────────────
-- The password prompt closes this panel, and dnf may outlast runAsync's 60 s.
-- So the engine writes its progress to statePath(), and the shell reopens the
-- panel when it is done; onOpen and update() pick the answer up.

local function finish(result)
  local state = statePath()
  if state ~= nil then
    noctalia.removeFile(state)
  end
  panel.setWantsSecondTicks(false)
  S.job, S.result, S.toTrash = nil, result, {}
  for _, gone in ipairs(result.removed or {}) do
    S.picked[gone.key] = nil
  end
  local n = #(result.removed or {})
  if n > 0 then
    noctalia.notify(tr("title"), fmt(n == 1 and "removed_one" or "removed_many", { n = n }))
  elseif not result.cancelled then
    noctalia.notifyError(tr("title"), tostring(result.error or tr("error")))
  end
  setStatus("")
  render()
end

function update()
  local state = statePath()
  if S.job == nil or state == nil or not noctalia.fileExists(state) then
    return
  end
  local info = noctalia.json.decode(noctalia.readFile(state) or "")
  if type(info) ~= "table" then
    return
  end
  S.job.info = info
  if info.state == "done" then
    S.log = type(info.log) == "table" and info.log or {}
    finish(type(info.result) == "table" and info.result or { ok = false, error = tr("error") })
  else
    render()
  end
end

-- ── actions the views call ──────────────────────────────────────────────────

-- Icons are looked up a few per frame, never in a render: a hundred lookups
-- in one callback blow Luau's CPU budget and the list never draws. Rows show
-- a placeholder glyph until theirs arrives.
local ICON_MS_PER_FRAME = 6 -- of the 25 ms budget; a lookup can take a few ms
local iconQueue = {}    -- items waiting for a lookup

local function lookupIcon(item)
  local icon = item.icon or ""
  if string.sub(icon, 1, 1) == "/" then
    return noctalia.fileExists(icon) and icon or false
  end
  return noctalia.appIconPath((item.desktop or "") ~= "" and item.desktop or icon, ICON_PX)
    or (icon ~= "" and icon ~= item.desktop and noctalia.appIconPath(icon, ICON_PX)) or false
end

function A.icon(item)
  local cached = icons[item.key]
  if cached == nil then
    icons[item.key] = "pending"
    table.insert(iconQueue, item)
    panel.setNeedsFrameTick(true)
    return nil
  end
  return (cached ~= false and cached ~= "pending") and cached or nil
end

function onFrameTick(_deltaMs)
  -- A frame that looks icons up does not also redraw: the two together could
  -- cross the budget. The redraw gets a frame of its own once all are in.
  if #iconQueue > 0 then
    local start = noctalia.nowMs()
    while #iconQueue > 0 and noctalia.nowMs() - start < ICON_MS_PER_FRAME do
      local item = table.remove(iconQueue, 1)
      icons[item.key] = lookupIcon(item)
    end
  else
    panel.setNeedsFrameTick(false)
    render()
  end
end

function A.setQuery(value)
  S.query, S.shown = value, PAGE
  render()
end

function A.setFilter(index)
  S.filter, S.shown = index, PAGE
  render()
end

function A.setMode(mode)
  if S.mode == mode then
    return
  end
  S.mode, S.shown, S.items = mode, PAGE, {}
  loadList()
end

-- A new column starts the way people look for it: names A to Z, the
-- biggest first (that is what frees space). Clicking it again flips it.
function A.sortBy(by)
  if S.sort.by == by then
    S.sort.desc = not S.sort.desc
  else
    S.sort = { by = by, desc = by == "size" }
  end
  S.shown = PAGE
  render()
end

function A.more()
  S.shown += PAGE
  render()
end

function A.toggle(item)
  S.picked[item.key] = S.picked[item.key] == nil and item or nil
  render()
end

function A.clear()
  S.picked = {}
  render()
end

function A.review()
  local keys = pickedKeys()
  if #keys == 0 or S.busy then
    return
  end
  S.view, S.preview, S.armed, S.withFiles = "review", nil, false, {}
  S.deleteData = shared.setting("flatpak_delete_data", false) == true
  S.busy = true
  setStatus("")
  render()
  call(`preview {quoted(keys)}`, function(result, err)
    S.busy = false
    if S.view ~= "review" then
      return
    end
    if result == nil then
      S.view = "list"
      fail(err)
      return
    end
    S.preview = result
    render()
  end)
end

function A.back()
  S.view, S.preview, S.armed = "list", nil, false
  render()
end

function A.setDeleteData(value)
  S.deleteData = value == true
  render()
end

function A.toggleFiles(key)
  S.withFiles[key] = not S.withFiles[key] or nil
  render()
end

function A.arm()
  S.armed = true
  render()
end

function A.remove()
  local state, keys = statePath(), pickedKeys()
  if state == nil or #keys == 0 or S.job ~= nil then
    return
  end
  local elevate = shared.trim(shared.setting("elevate_command", ""))
  local tool = string.match(elevate ~= "" and elevate or "pkexec", "^(%S+)")
  if not noctalia.commandExists(tool) then
    fail(fmt("elevate_missing", { command = tool }))
    return
  end
  noctalia.removeFile(state)
  S.view, S.job, S.result, S.armed = "job", { info = {} }, nil, false
  panel.setWantsSecondTicks(true)
  -- `;` before panel-open, not `&&`: a failed or cancelled removal must still
  -- bring the panel back to say so.
  -- --reopen: the engine brings the panel back as soon as the password is
  -- in, so the progress shows while dnf runs. The panel-open after the `;`
  -- covers a cancelled prompt, where dnf never starts.
  -- Only keys go across: the engine works the app files out again itself.
  local withFiles = ""
  for key in pairs(S.withFiles) do
    withFiles ..= ` --with-files {shellQuote(key)}`
  end
  noctalia.runAsync(`{shared.engine()} remove {quoted(keys)} --state {shellQuote(state)}`
    .. ` --reopen {shellQuote(PANEL_ID)}{withFiles}`
    .. (S.deleteData and " --delete-data" or "")
    .. ` > /dev/null 2>&1; noctalia msg panel-open {shellQuote(PANEL_ID)}`)
  render()
end

function A.toggleLog()
  S.showLog = not S.showLog
  render()
end

function A.toggleLeftover(path)
  S.toTrash[path] = not S.toTrash[path] or nil
  render()
end

function A.done()
  S.view, S.result, S.preview, S.toTrash = "list", nil, nil, {}
  setStatus("")
  loadList()
end

function A.trash()
  local paths = {}
  for _, dir in ipairs((S.result or {}).leftovers or {}) do
    if S.toTrash[dir.path] then
      table.insert(paths, dir.path)
    end
  end
  if #paths == 0 or S.busy then
    return
  end
  S.busy = true
  render()
  call(`trash {quoted(paths)}`, function(result, err)
    S.busy = false
    if result == nil then
      fail(err)
      return
    end
    local n = #(result.trashed or {})
    noctalia.notify(tr("title"), fmt(n == 1 and "trashed_one" or "trashed_many", { n = n }))
    A.done()
  end)
end

-- ── frame ───────────────────────────────────────────────────────────────────

render = function()
  local body
  if S.view == "job" then
    body = jobView.render(S, A)
  elseif S.view == "review" then
    body = reviewView.render(S, A)
  else
    body = listView.render(S, A)
  end
  panel.render(ui.column({ flexGrow = 1, gap = 14, align = "stretch" }, {
    ui.row({ gap = 8, align = "center" }, {
      ui.label({ text = tr("title"), fontSize = 15, fontWeight = "semibold", flexGrow = 1 }),
      ui.button({ key = "reload", glyph = "refresh", variant = "ghost", tooltip = tr("reload"),
                  enabled = S.view == "list" and not S.busy, onClick = function() loadList() end }),
      ui.button({ key = "close", glyph = "x", variant = "ghost", tooltip = tr("close"),
                  onClick = function() panel.close() end }),
    }),
    body,
  }))
end

-- ── lifecycle ───────────────────────────────────────────────────────────────

function onOpen(_context)
  if S.mode == nil then
    S.mode = shared.setting("default_view", "apps") == "packages" and "packages" or "apps"
  end
  -- Lookups queued before the panel closed are still owed.
  if #iconQueue > 0 then
    panel.setNeedsFrameTick(true)
  end
  -- A removal is running, or finished while the panel was closed.
  local state = statePath()
  if S.job ~= nil or (state ~= nil and noctalia.fileExists(state)) then
    S.view, S.job = "job", S.job or { info = {} }
    panel.setWantsSecondTicks(true)
    update()
    render()
    return
  end
  -- Its leftovers are still waiting for an answer.
  if S.view == "job" and S.result ~= nil then
    render()
    return
  end
  S.view, S.preview, S.armed = "list", nil, false
  loadList()
end
