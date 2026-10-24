--!nonstrict
-- Control centre tile that opens the app remover panel.

shortcut.setLabel(noctalia.tr("title"))
shortcut.setIcon("trash")

function onClick()
  noctalia.togglePanel("vn1k/app-remover:manager")
end
