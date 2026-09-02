#!/usr/bin/env python3
"""Minimal GTK3 window with a named Entry — AT-SPI set_value target (GBM-24)."""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

win = Gtk.Window(title="GBM24")
win.set_default_size(420, 140)
win.connect("destroy", Gtk.main_quit)

box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
box.set_margin_top(16)
box.set_margin_bottom(16)
box.set_margin_start(16)
box.set_margin_end(16)

label = Gtk.Label(label="Value")
label.set_xalign(0)
entry = Gtk.Entry()
entry.set_placeholder_text("type here")
entry.set_text("")
try:
    entry.get_accessible().set_name("Value")
except Exception:
    pass

btn = Gtk.Button(label="OK")

box.pack_start(label, False, False, 0)
box.pack_start(entry, False, False, 0)
box.pack_start(btn, False, False, 0)
win.add(box)
win.show_all()
Gtk.main()
