--!nonstrict
-- Cursor theme panel.
--
-- Every cursor file and every config write belongs to the curmgr CLI; this
-- file only shells out to it and renders the JSON it prints. That split keeps
-- binary parsing and five-layer config writing out of a sandboxed script, and
-- means the same engine works from a keybind without the shell running.

local PANEL_ID = "vn1k/cursor:manager"
local SIZES = { 16, 20, 24, 28, 32, 40, 48, 64 }
local COLUMNS = 2
local PANEL_WIDTH = 780
local PANEL_PADDING = 28 -- Style::panelPadding * 2 on the host surface
local GRID_GAP = 12
local STRIP_HEIGHT = 56

local tab = "themes"
local themes = {}
local previews = {} -- theme name -> rendered strip path
local selected = ""
local sizeIndex = 2 -- 24
local hideTyping = false
local hideMs = ""
local busy = false
local status = ""
local statusTone = "on_surface_variant"
local current = nil

local importPath, importName = "", ""
local importShadow = false
local buildPath, buildName = "", ""
-- No name field: an installed theme carries its own name. `curmgr install
-- --name` still exists for the CLI when two themes would collide.
local installPath = ""
-- Deleting a theme is destructive and a dialog would dismiss the panel, so the
-- confirmation is the button itself: first click arms, second click removes.
local pendingRemove = ""
local report = nil
-- ui.input only reads `value` when it mounts, so a path chosen by the picker
-- needs a new key to land in the field at all.
local pickSeq = 0

local render
local loadThemes

local function tr(key)
  return noctalia.tr(key)
end

local function shellQuote(s)
  return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end

local function trim(s)
  return noctalia.string.trim(tostring(s or ""))
end

local function configString(key, fallback)
  local value = noctalia.getConfig(key)
  if type(value) == "string" and value ~= "" then
    return value
  end
  return fallback
end

-- The engine ships inside the plugin, so a store install needs nothing on
-- PATH; the setting only exists for a checkout that keeps curmgr elsewhere.
local function engine()
  local override = configString("engine_path", "")
  if override ~= "" then
    return shellQuote(noctalia.expandPath(override))
  end
  local dir = noctalia.pluginDir()
  if type(dir) == "string" and dir ~= "" then
    return `python3 -B {shellQuote(dir .. "/bin/curmgr.py")}`
  end
  return "curmgr"
end

local function imageArgs()
  return ` --sizes {shellQuote(configString("build_sizes", "24,32,48,64,96"))}`
    .. ` --filter {shellQuote(configString("scale_filter", "lanczos"))}`
end

-- Every subcommand answers with one JSON object, so a decode failure means the
-- engine itself is missing or broken rather than the request being invalid.
local function call(args, done)
  if configString("engine_path", "") == "" and not noctalia.commandExists("python3") then
    done(nil, tr("engine_missing"))
    return
  end
  noctalia.runAsync(`{engine()} {args}`, function(result)
    if result == nil or result.timedOut then
      done(nil, tr("engine_missing"))
      return
    end
    local decoded = noctalia.json.decode(result.stdout or "")
    if type(decoded) ~= "table" then
      local stderr = trim(result.stderr)
      done(nil, stderr ~= "" and stderr or tr("engine_missing"))
      return
    end
    if not decoded.ok then
      done(nil, tostring(decoded.error or tr("error")))
      return
    end
    done(decoded, nil)
  end)
end

local function setStatus(text, tone)
  status = text
  statusTone = tone or "on_surface_variant"
end

local function fail(message)
  busy = false
  setStatus(message, "error")
  noctalia.notifyError(tr("title"), message)
  render()
end

-- ── file picker ─────────────────────────────────────────────────────────────
-- Opening zenity takes keyboard focus, and a Noctalia panel dismisses when it
-- loses focus, so the panel is gone before the user has picked anything. The
-- pick is therefore fire-and-forget rather than a runAsync callback: the shell
-- stashes the chosen path in the plugin data dir and reopens the panel itself,
-- which also dodges the 60s ceiling runAsync puts on a callback.

local function stashPath(field)
  local dir = noctalia.pluginDataDir()
  if dir == nil then
    return nil
  end
  return `{dir}/pick-{field}`
end

local function startDir(current)
  local path = trim(current)
  if path ~= "" then
    return noctalia.expandPath(path)
  end
  -- Trailing slash so zenity opens inside the folder rather than selecting it.
  return noctalia.expandPath("~/Downloads") .. "/"
end

-- Every tab takes a folder: extract an archive first. The engine still accepts
-- one, so a .zip or .tar.* path typed into the field keeps working.
local function pick(field, current)
  if not noctalia.commandExists("zenity") then
    fail(tr("picker_missing"))
    return
  end
  local stash = stashPath(field)
  if stash == nil then
    fail(tr("picker_missing"))
    return
  end
  -- `;` before panel-open, not `&&`: cancelling must still bring the panel back.
  noctalia.runAsync(
    `p=$(zenity --file-selection --directory --title={shellQuote(tr("pick_folder"))}`
      .. ` --filename={shellQuote(startDir(current))}) && printf '%s' "$p" > {shellQuote(stash)}`
      .. `; noctalia msg panel-open {shellQuote(PANEL_ID)}`
  )
end

-- Called on every open: a stash file means the user just came back from a dialog.
local function consumePick(field, apply)
  local stash = stashPath(field)
  if stash == nil or not noctalia.fileExists(stash) then
    return
  end
  local contents = noctalia.readFile(stash)
  noctalia.removeFile(stash)
  local path = trim(contents)
  if path ~= "" then
    apply(path)
    pickSeq += 1
  end
end

local function sizeValue()
  return SIZES[sizeIndex + 1] or 24
end

local function selectSize(value)
  for index, size in ipairs(SIZES) do
    if size == value then
      sizeIndex = index - 1
      return
    end
  end
end

-- Previews are rendered one at a time: each is a fresh process, and a theme
-- with a hundred cursors is still under a second.
local function loadPreviews(index)
  local theme = themes[index]
  if theme == nil then
    busy = false
    render()
    return
  end
  call(`preview {shellQuote(theme.name)}`, function(result, err)
    if result ~= nil then
      previews[theme.name] = result.preview
      render()
    elseif index == 1 then
      -- Previews are optional: without wand the panel still lists and applies
      -- themes, so explain the blank strips rather than failing the panel. Only
      -- the first theme reports; a missing dependency fails all of them.
      setStatus(err, "tertiary")
    end
    loadPreviews(index + 1)
  end)
end

local function loadCurrent(done)
  call("current", function(result)
    if result ~= nil then
      current = result
      if selected == "" then
        selected = result.theme or ""
      end
      selectSize(tonumber(result.size) or 24)
      hideTyping = result.hide_when_typing == true
      local ms = tonumber(result.hide_after_inactive_ms) or 0
      hideMs = ms > 0 and tostring(ms) or ""
    end
    if done ~= nil then
      done()
    end
  end)
end

loadThemes = function()
  busy = true
  -- Panel state outlives a close, so a reopen would otherwise draw last
  -- session's drift warning until the refresh lands.
  current = nil
  setStatus(tr("loading"))
  render()
  call("list", function(result, err)
    if result == nil then
      fail(err)
      return
    end
    themes = result.themes or {}
    setStatus("")
    loadCurrent(function()
      render()
      loadPreviews(1)
    end)
  end)
end

-- ── rendering ───────────────────────────────────────────────────────────────

local function tileWidth()
  return math.floor((PANEL_WIDTH - PANEL_PADDING - GRID_GAP * (COLUMNS - 1)) / COLUMNS)
end

local function themeTile(theme)
  local isSelected = theme.name == selected
  local width = tileWidth()
  local preview = previews[theme.name]
  local border = isSelected and "primary" or "outline"
  local borderWidth = isSelected and 3 or 1
  local onClick = function()
    selected = theme.name
    render()
  end

  local frame
  if preview ~= nil then
    frame = ui.image({
      width = width, height = STRIP_HEIGHT, path = preview, fit = "contain",
      radius = 12, border = border, borderWidth = borderWidth, onClick = onClick,
    })
  else
    frame = ui.box({
      width = width, height = STRIP_HEIGHT, fill = "surface_variant",
      radius = 12, border = border, borderWidth = borderWidth, onClick = onClick,
    })
  end

  local caption = theme.title or theme.name
  if current ~= nil and current.theme == theme.name then
    caption = `{caption}  ·  {tr("active")}`
  end

  return ui.column({ gap = 4, key = theme.name, flexGrow = 1, align = "center" }, {
    frame,
    ui.label({
      text = caption, fontSize = 12, maxLines = 1, maxWidth = width,
      textAlign = "center",
      color = isSelected and "on_surface" or "on_surface_variant",
      fontWeight = isSelected and "bold" or "normal",
    }),
  })
end

local function themeGrid()
  if #themes == 0 then
    return ui.label({ text = busy and tr("loading") or tr("no_themes"), color = "on_surface_variant" })
  end
  local rows, row = {}, {}
  for index, theme in ipairs(themes) do
    table.insert(row, themeTile(theme))
    if #row == COLUMNS or index == #themes then
      table.insert(rows, ui.row({ gap = GRID_GAP, align = "stretch" }, row))
      row = {}
    end
  end
  return ui.column({ gap = GRID_GAP, align = "stretch" }, rows)
end

local function sizeLabels()
  local labels = {}
  for index, size in ipairs(SIZES) do
    labels[index] = tostring(size)
  end
  return labels
end

-- The whole point of the plugin: one theme name written to five places. If a
-- layer drifted, say so rather than letting the cursor silently differ per app.
local function driftWarning()
  if current == nil or current.consistent or busy then
    return ui.box({ height = 0 })
  end
  return ui.row({ gap = 8, align = "center" }, {
    ui.glyph({ name = "alert-triangle", color = "tertiary", size = 16 }),
    ui.label({ text = tr("drift"), fontSize = 12, color = "tertiary", flexGrow = 1 }),
  })
end

local function themesTab()
  return ui.column({ flexGrow = 1, gap = 12, align = "stretch" }, {
    driftWarning(),
    -- ui.scroll centres content shorter than its viewport, so a one-theme list
    -- sits mid-panel. It fills from the top as soon as the grid is taller.
    ui.scroll({ flexGrow = 1, gap = GRID_GAP }, { themeGrid() }),
    ui.separator({}),
    ui.row({ gap = 12, align = "center" }, {
      ui.label({ text = tr("size"), fontSize = 12, color = "on_surface_variant" }),
      ui.select({ key = "size", options = sizeLabels(), selectedIndex = sizeIndex,
                  onChange = "onSizeChange", width = 96 }),
      ui.button({ key = "hide_typing", text = tr("hide_when_typing"),
                  variant = hideTyping and "primary" or "outline",
                  onClick = "onToggleHideTyping" }),
      ui.input({ key = "hide_ms", placeholder = tr("hide_after_ms"), value = hideMs,
                 width = 150, onChange = "onHideMsChange" }),
      ui.spacer({ flexGrow = 1 }),
      ui.button({ key = "apply", text = tr("apply"), variant = "primary",
                  onClick = "onApply", enabled = not busy and selected ~= "" }),
    }),
  })
end

local function reportView()
  if report == nil then
    return ui.box({ height = 0 })
  end
  local lines = {}
  if report.method ~= nil then
    table.insert(lines, ui.label({ text = `{tr("method")}: {report.method}`,
                                   fontSize = 12, color = "on_surface_variant" }))
  end
  if report.mapped ~= nil and #report.mapped > 0 then
    table.insert(lines, ui.label({ text = `{tr("mapped")}: {table.concat(report.mapped, ", ")}`,
                                   fontSize = 12, color = "on_surface_variant" }))
  end
  if report.unmapped_roles ~= nil and #report.unmapped_roles > 0 then
    table.insert(lines, ui.label({
      text = `{tr("unmapped")}: {table.concat(report.unmapped_roles, ", ")}`,
      fontSize = 12, color = "tertiary",
    }))
    table.insert(lines, ui.label({ text = tr("unmapped_hint"), fontSize = 11,
                                   color = "on_surface_variant" }))
  end
  if #lines == 0 then
    return ui.box({ height = 0 })
  end
  return ui.column({ gap = 4, align = "stretch" }, lines)
end

local function importTab()
  return ui.column({ flexGrow = 1, gap = 12, align = "stretch" }, {
    ui.label({ text = tr("import_hint"), fontSize = 12, color = "on_surface_variant" }),
    ui.input({ key = `import_path_{pickSeq}`, placeholder = tr("import_path"), value = importPath,
               onChange = "onImportPathChange", onSubmit = "onImportPathChange" }),
    ui.row({ gap = 8, align = "center" }, {
      ui.button({ key = "pick_import_dir", text = tr("pick_folder"), glyph = "folder",
                  variant = "outline", controlSize = "sm", onClick = "onPickImportDir" }),
      ui.spacer({ flexGrow = 1 }),
    }),
    ui.input({ key = "import_name", placeholder = tr("theme_name"), value = importName,
               onChange = "onImportNameChange", onSubmit = "onImportNameChange" }),
    ui.row({ gap = 12, align = "center" }, {
      ui.button({ key = "shadow", text = tr("shadow"),
                  variant = importShadow and "primary" or "outline",
                  tooltip = tr("shadow_tooltip"), onClick = "onToggleShadow" }),
      ui.spacer({ flexGrow = 1 }),
      ui.button({ key = "convert", text = tr("convert"), variant = "primary",
                  onClick = "onImport", enabled = not busy and trim(importPath) ~= "" }),
    }),
    ui.scroll({ flexGrow = 1, gap = 8 }, { reportView() }),
  })
end

local function buildTab()
  return ui.column({ flexGrow = 1, gap = 12, align = "stretch" }, {
    ui.label({ text = tr("build_hint"), fontSize = 12, color = "on_surface_variant" }),
    ui.input({ key = `build_path_{pickSeq}`, placeholder = tr("build_path"), value = buildPath,
               onChange = "onBuildPathChange", onSubmit = "onBuildPathChange" }),
    ui.row({ gap = 8, align = "center" }, {
      ui.button({ key = "pick_build_dir", text = tr("pick_folder"), glyph = "folder",
                  variant = "outline", controlSize = "sm", onClick = "onPickBuildDir" }),
      ui.spacer({ flexGrow = 1 }),
    }),
    ui.input({ key = "build_name", placeholder = tr("theme_name"), value = buildName,
               onChange = "onBuildNameChange", onSubmit = "onBuildNameChange" }),
    ui.row({ gap = 12, align = "center" }, {
      ui.spacer({ flexGrow = 1 }),
      ui.button({ key = "build", text = tr("build"), variant = "primary",
                  onClick = "onBuild", enabled = not busy and trim(buildPath) ~= "" }),
    }),
    ui.scroll({ flexGrow = 1, gap = 8 }, { reportView() }),
  })
end

-- Only themes under ~/.local/share/icons are ours to delete; list_themes marks
-- those `removable`, so system themes simply never appear here.
local function removableRows()
  local rows = {}
  for _, theme in ipairs(themes) do
    if theme.removable then
      local name = theme.name
      local armed = pendingRemove == name
      table.insert(rows, ui.row({ key = `rm_{name}`, gap = 8, align = "center" }, {
        ui.label({ text = theme.title or name, fontSize = 13, maxLines = 1, flexGrow = 1 }),
        ui.label({ text = tostring(theme.count), fontSize = 11, color = "on_surface_variant" }),
        ui.button({
          key = `rmbtn_{name}`,
          text = armed and tr("confirm_remove") or nil,
          glyph = not armed and "trash" or nil,
          variant = armed and "destructive" or "ghost",
          controlSize = "sm", enabled = not busy,
          tooltip = tr("remove"),
          onClick = function()
            if armed then
              onRemoveTheme(name)
            else
              pendingRemove = name
              render()
            end
          end,
        }),
      }))
    end
  end
  if #rows == 0 then
    return { ui.label({ text = tr("none_removable"), fontSize = 12,
                        color = "on_surface_variant" }) }
  end
  return rows
end

local function installTab()
  return ui.column({ flexGrow = 1, gap = 12, align = "stretch" }, {
    ui.label({ text = tr("install_hint"), fontSize = 12, color = "on_surface_variant" }),
    ui.input({ key = `install_path_{pickSeq}`, placeholder = tr("install_path"),
               value = installPath, onChange = "onInstallPathChange",
               onSubmit = "onInstallPathChange" }),
    ui.row({ gap = 8, align = "center" }, {
      ui.button({ key = "pick_install_dir", text = tr("pick_folder"), glyph = "folder",
                  variant = "outline", controlSize = "sm", onClick = "onPickInstallDir" }),
      ui.spacer({ flexGrow = 1 }),
      ui.button({ key = "install", text = tr("install"), variant = "primary",
                  onClick = "onInstall", enabled = not busy and trim(installPath) ~= "" }),
    }),
    ui.separator({}),
    ui.label({ text = tr("installed_themes"), fontSize = 12, fontWeight = "bold" }),
    ui.scroll({ flexGrow = 1, gap = 6 }, removableRows()),
  })
end

local function tabButton(id, labelKey)
  return ui.button({
    key = `tab_{id}`, text = tr(labelKey),
    variant = tab == id and "primary" or "ghost",
    onClick = function()
      tab = id
      render()
    end,
  })
end

render = function()
  local body
  if tab == "import" then
    body = importTab()
  elseif tab == "build" then
    body = buildTab()
  elseif tab == "install" then
    body = installTab()
  else
    body = themesTab()
  end

  panel.render(ui.column({ flexGrow = 1, gap = 12, align = "stretch" }, {
    ui.row({ align = "center", justify = "space_between", gap = 8 }, {
      ui.label({ text = tr("title"), fontSize = 16, fontWeight = "bold",
                 color = "on_surface", flexGrow = 1 }),
      ui.button({ key = "reload", glyph = "refresh", variant = "ghost",
                  tooltip = tr("reload"), onClick = "onReload", enabled = not busy }),
      ui.button({ key = "close", glyph = "close", onClick = "onCloseClicked" }),
    }),
    ui.row({ gap = 8, align = "center" }, {
      tabButton("themes", "tab_themes"),
      tabButton("install", "tab_install"),
      tabButton("import", "tab_import"),
      tabButton("build", "tab_build"),
      ui.spacer({ flexGrow = 1 }),
      ui.label({ text = status, fontSize = 12, color = statusTone, maxLines = 1 }),
    }),
    body,
  }))
end

-- ── handlers ────────────────────────────────────────────────────────────────

function onOpen(_context)
  -- The picker reopens this panel when it is done, so an open is also how a
  -- chosen path comes back.
  consumePick("import", function(path)
    importPath = path
    tab = "import"
  end)
  consumePick("build", function(path)
    buildPath = path
    tab = "build"
  end)
  consumePick("install", function(path)
    installPath = path
    tab = "install"
  end)
  loadThemes()
end

function onPickImportDir()
  pick("import", importPath)
end

function onPickBuildDir()
  pick("build", buildPath)
end

function onPickInstallDir()
  pick("install", installPath)
end

function onInstallPathChange(value)
  installPath = trim(value)
end

function onInstall()
  local source = trim(installPath)
  if source == "" or busy then
    return
  end
  if not noctalia.fileExists(noctalia.expandPath(source)) then
    fail(tr("path_missing"))
    return
  end
  busy = true
  pendingRemove = ""
  setStatus(tr("installing"))
  render()

  call(`install {shellQuote(noctalia.expandPath(source))}`, function(result, err)
    if result == nil then
      fail(err)
      return
    end
    busy = false
    local names = table.concat(result.installed, ", ")
    local verb = #result.replaced > 0 and tr("replaced") or tr("installed")
    setStatus(`{verb}: {names}`, "primary")
    noctalia.notify(tr("title"), `{verb}: {names}`)
    selected = result.installed[1] or selected
    loadThemes()
  end)
end

function onRemoveTheme(name)
  if busy then
    return
  end
  busy = true
  pendingRemove = ""
  setStatus(tr("removing"))
  render()

  call(`remove {shellQuote(name)}`, function(result, err)
    if result == nil then
      fail(err)
      return
    end
    busy = false
    setStatus(`{tr("removed")}: {name}`, "primary")
    -- The removed theme may still be the one every layer points at; a refresh
    -- makes the drift warning tell the truth.
    loadThemes()
  end)
end

function onReload()
  previews = {}
  loadThemes()
end

function onCloseClicked()
  panel.close()
end

function onSizeChange(index, _text)
  sizeIndex = tonumber(index) or 2
  render()
end

function onToggleHideTyping()
  hideTyping = not hideTyping
  render()
end

function onHideMsChange(value)
  hideMs = trim(value)
end

function onApply()
  if selected == "" or busy then
    return
  end
  busy = true
  setStatus(tr("applying"))
  render()

  local args = `apply {shellQuote(selected)} --size {sizeValue()}`
  if hideTyping then
    args ..= " --hide-when-typing"
  end
  local ms = tonumber(hideMs)
  if ms ~= nil and ms > 0 then
    args ..= ` --hide-after-inactive-ms {math.floor(ms)}`
  end

  call(args, function(result, err)
    if result == nil then
      fail(err)
      return
    end
    busy = false
    setStatus(tr("applied"), "primary")
    noctalia.notify(tr("title"), `{tr("applied")}: {selected}`)
    loadCurrent(render)
  end)
end

function onImportPathChange(value)
  importPath = trim(value)
end

function onImportNameChange(value)
  importName = trim(value)
end

function onToggleShadow()
  importShadow = not importShadow
  render()
end

function onImport()
  local source = trim(importPath)
  if source == "" or busy then
    return
  end
  if not noctalia.fileExists(noctalia.expandPath(source)) then
    fail(tr("path_missing"))
    return
  end
  busy = true
  report = nil
  setStatus(tr("converting"))
  render()

  local args = `import-win {shellQuote(noctalia.expandPath(source))}{imageArgs()}`
  if importName ~= "" then
    args ..= ` --name {shellQuote(importName)}`
  end
  if importShadow then
    args ..= " --shadow"
  end

  call(args, function(result, err)
    if result == nil then
      fail(err)
      return
    end
    busy = false
    report = result
    selected = result.name
    setStatus(`{tr("built")}: {result.name}`, "primary")
    noctalia.notify(tr("title"), `{tr("built")}: {result.name}`)
    tab = "themes"
    loadThemes()
  end)
end

function onBuildPathChange(value)
  buildPath = trim(value)
end

function onBuildNameChange(value)
  buildName = trim(value)
end

function onBuild()
  local source = trim(buildPath)
  if source == "" or busy then
    return
  end
  -- build only ever reads a directory of PNGs, so say so here rather than
  -- letting curmgr reject it after the round trip.
  local info = noctalia.fileInfo(noctalia.expandPath(source))
  if info == nil then
    fail(tr("path_missing"))
    return
  end
  if not info.isDir then
    fail(tr("not_a_folder"))
    return
  end
  busy = true
  report = nil
  setStatus(tr("building"))
  render()

  local args = `build {shellQuote(noctalia.expandPath(source))}{imageArgs()}`
  if buildName ~= "" then
    args ..= ` --name {shellQuote(buildName)}`
  end

  call(args, function(result, err)
    if result == nil then
      fail(err)
      return
    end
    busy = false
    report = result
    selected = result.name
    setStatus(`{tr("built")}: {result.name}`, "primary")
    noctalia.notify(tr("title"), `{tr("built")}: {result.name}`)
    tab = "themes"
    loadThemes()
  end)
end
