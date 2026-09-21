--!nonstrict
-- Control centre tile that opens the cursor panel.

local function render()
  shortcut.setLabel(noctalia.tr("title"))
  shortcut.setIcon("cursor")
end

render()

function onClick()
  noctalia.togglePanel("vinik/cursor:manager")
end
