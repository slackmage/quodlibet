#!/usr/bin/env python

# Copyright 2004 Joe Wreschnig, Michael Urman
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
#
# $Id$

import pygtk
pygtk.require('2.0')
import gtk
import gtk.glade
from library import library
import player
import threading
import gc
import os
import util; from util import escape
import signal
import config
import time

# This object communicates with the playing thread. It's the only way
# the playing thread talks to the UI, so replacing this with something
# using e.g. Curses would change the UI. The converse is not true. Many
# parts of the UI talk to the player.
#
# The single instantiation of this is widgets.wrap, created at startup.
class GTKSongInfoWrapper(object):
    def __init__(self):
        self.image = widgets["albumcover"]
        self.vbar = widgets["vseparator2"]
        self.text = widgets["currentsong"]
        self.pos = widgets["song_pos"]
        self.timer = widgets["song_timer"]
        self.button = widgets["play_button"]
        self.playing = gtk.gdk.pixbuf_new_from_file("pause.png")
        self.paused = gtk.gdk.pixbuf_new_from_file("play.png")

        self._time = (0, 1)
        gtk.timeout_add(300, self._update_time)

    # The pattern of getting a call from the playing thread and then
    # queueing an idle function prevents thread-unsafety in GDK.

    # The pause toggle was clicked.
    def set_paused(self, paused):
        gtk.idle_add(self._update_paused, paused)

    def _update_paused(self, paused):
        img = self.button.get_icon_widget()
        if paused: img.set_from_pixbuf(self.paused)
        else: img.set_from_pixbuf(self.playing)

    # The player told us about a new time.
    def set_time(self, cur, end):
        self._time = (cur, end)

    def _update_time(self):
        cur, end = self._time
        self.pos.set_value(cur)
        self.timer.set_text("%d:%02d/%d:%02d" %
                            (cur / 60000, (cur % 60000) / 1000,
                             end / 60000, (end % 60000) / 1000))
        return True

    # A new song was selected, or the next song started playing.
    def set_song(self, song, player):
        gtk.idle_add(self._update_song, song, player)

    # Called when no cover is available, or covers are off.
    def disable_cover(self):
        self.image.hide()
        self.vbar.hide()

    # Called when a covers are turned on; an image may not be available.
    def enable_cover(self):
        if self.image.get_pixbuf():
            self.image.show()
            self.vbar.show()

    def _update_song(self, song, player):
        if song:
            self.pos.set_range(0, player.length)
            self.pos.set_value(0)

            cover = song.find_cover()
            if cover:
                pixbuf = gtk.gdk.pixbuf_new_from_file(cover)
                pixbuf = pixbuf.scale_simple(100, 100, gtk.gdk.INTERP_BILINEAR)
                self.image.set_from_pixbuf(pixbuf)
                if config.state("cover"): self.enable_cover()
            else:
                self.image.set_from_pixbuf(None)
                self.disable_cover()
            self.text.set_markup(song.to_markup())
        else:
            self.image.set_from_pixbuf(None)
            self.pos.set_range(0, 1)
            self.pos.set_value(0)
            self._time = (0, 1)
            self.text.set_markup("<span size='xx-large'>Not playing</span>")

        # Update the currently-playing song in the list by bolding it.
        last_song = CURRENT_SONG[0]
        CURRENT_SONG[0] = song
        col = len(HEADERS)
        def update_if_last_or_current(model, path, iter):
            if model[iter][col] is song:
                model[iter][col + 1] = 700
                model.row_changed(path, iter)
            elif model[iter][col] is last_song:
                model[iter][col + 1] = 400
                model.row_changed(path, iter)
        widgets.songs.foreach(update_if_last_or_current)

        return False

# Make a standard directory-chooser, and return the filenames and response.
def make_chooser(title):
    chooser = gtk.FileChooserDialog(
        title = title,
        action = gtk.FILE_CHOOSER_ACTION_SELECT_FOLDER,
        buttons = (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                   gtk.STOCK_OPEN, gtk.RESPONSE_OK))
    chooser.set_select_multiple(True)
    resp = chooser.run()
    fns = chooser.get_filenames()
    chooser.destroy()
    return resp, fns

# Standard Glade widgets wrapper.
class Widgets(object):
    def __init__(self, file):
        self.widgets = gtk.glade.XML("quodlibet.glade")
        self.widgets.signal_autoconnect(GladeHandlers.__dict__)

    def __getitem__(self, key):
        return self.widgets.get_widget(key)

# Glade-connected handler functions.
class GladeHandlers(object):
    def gtk_main_quit(*args): gtk.main_quit()

    def play_pause(button):
        player.playlist.paused ^= True

    def next_song(*args):
        player.playlist.next()

    def previous_song(*args):
        player.playlist.previous()

    def toggle_repeat(button):
        player.playlist.repeat = button.get_active()

    def show_about(menuitem):
        widgets["about_window"].set_transient_for(widgets["main_window"])
        widgets["about_window"].show()

    def close_about(*args):
        widgets["about_window"].hide()
        return True

    def toggle_shuffle(button):
        player.playlist.shuffle = button.get_active()

    def seek_slider(slider, v):
        gtk.idle_add(player.playlist.seek, v)

    # Set up the preferences window.
    def open_prefs(*args):
        widgets["prefs_window"].set_transient_for(widgets["main_window"])
        # Fill in the general checkboxes.
        widgets["cover_t"].set_active(config.state("cover"))
        widgets["color_t"].set_active(config.state("color"))
        old_h = HEADERS[:]

        # Fill in the header checkboxes.
        widgets["track_t"].set_active("=#" in old_h)
        widgets["album_t"].set_active("album" in old_h)
        widgets["artist_t"].set_active("artist" in old_h)
        widgets["genre_t"].set_active("genre" in old_h)
        widgets["year_t"].set_active("year" in old_h)
        widgets["version_t"].set_active("version" in old_h)
        widgets["performer_t"].set_active("performer" in old_h)

        # Remove the standard headers, and put the rest in the list.
        for t in ["=#", "album", "artist", "genre", "year", "version",
                  "performer", "title"]:
            try: old_h.remove(t)
            except ValueError: pass
        widgets["extra_headers"].set_text(" ".join(old_h))

        # Fill in the scanned directories.
        widgets["scan_opt"].set_text(config.get("settings", "scan"))
        widgets["prefs_window"].show()

    def set_headers(*args):
        # Based on the state of the checkboxes, set up new column headers.
        new_h = []
        if widgets["track_t"].get_active(): new_h.append("=#")
        new_h.append("title")
        if widgets["album_t"].get_active(): new_h.append("album")
        if widgets["artist_t"].get_active(): new_h.append("artist")
        if widgets["genre_t"].get_active(): new_h.append("genre")
        if widgets["year_t"].get_active(): new_h.append("year")
        if widgets["version_t"].get_active(): new_h.append("version")
        if widgets["performer_t"].get_active(): new_h.append("performer")
        new_h.extend(widgets["extra_headers"].get_text().split())
        HEADERS[:] = new_h
        set_column_headers(widgets["songlist"])

    def change_scan(*args):
        config.set("settings", "scan", widgets["scan_opt"].get_text())

    def toggle_color(toggle):
        config.set("settings", "color", str(bool(toggle.get_active())))

    def toggle_cover(toggle):
        config.set("settings", "cover", str(bool(toggle.get_active())))
        if config.state("cover"): widgets.wrap.enable_cover()
        else: widgets.wrap.disable_cover()

    def select_scan(*args):
        resp, fns = make_chooser("Select Directories")
        if resp == gtk.RESPONSE_OK:
            widgets["scan_opt"].set_text(":".join(fns))

    def prefs_closed(*args):
        widgets["prefs_window"].hide()
        config_fn = os.path.join(os.environ["HOME"], ".quodlibet", "config")
        f = file(config_fn, "w")
        config.write(f)
        f.close()
        return True

    def select_song(tree, indices, col):
        iter = widgets.songs.get_iter(indices)
        song = widgets.songs.get_value(iter, len(HEADERS))
        player.playlist.go_to(song)
        player.playlist.paused = False

    def open_chooser(*args):
        resp, fns = make_chooser("Add Music")
        if resp == gtk.RESPONSE_OK:
            library.scan(fns)
            songs = filter(CURRENT_FILTER[0], library.values())
            player.playlist.set_playlist(songs)
            refresh_songlist()
            gc.collect()

    def update_volume(slider):
        player.device.volume = int(slider.get_value())

# Non-Glade handlers:

# Grab the text from the query box, parse it, and make a new filter.
def text_parse(*args):
        from parser import QueryParser, QueryLexer
        text = widgets["query"].child.get_text().decode("utf-8")
        if text.strip() == "": # Empty text, remove all filters.
            CURRENT_FILTER[0] = FILTER_ALL
            songs = filter(CURRENT_FILTER[0], library.values())
            player.playlist.set_playlist(songs)
            refresh_songlist()
        else:
            if "=" not in text and "/" not in text:
                # A simple search, not regexp-based.
                widgets["query"].prepend_text(text)
                parts = ["* = /" + p + "/" for p in text.split()]
                text = "&(" + ",".join(parts) + ")"
                # The result must be well-formed, since no /s were
                # in the original string.
                q = QueryParser(QueryLexer(text)).Query()
            else:
                try:
                    # Regular query, but possibly not well-formed..
                    q = QueryParser(QueryLexer(text)).Query()
                    widgets["query"].prepend_text(text)
                except: return

            t = time.time()
            CURRENT_FILTER[0] = q.search
            set_entry_color(widgets["query"].child, "black")
            songs = filter(CURRENT_FILTER[0], library.values())
            player.playlist.set_playlist(songs)
            print "Searching songlist took %f seconds " % (time.time() - t)
            refresh_songlist()

# Try and construct a query, but don't actually run it; change the color
# of the textbox to indicate its validity (if the option to do so is on).
def test_filter(*args):
        if not config.state("color"): return
        from parser import QueryParser, QueryLexer
        textbox = widgets["query"].child
        text = textbox.get_text()
        if "=" not in text and "/" not in text:
            gtk.idle_add(set_entry_color, textbox, "blue")
        else:
            try:
                QueryParser(QueryLexer(text)).Query()
            except:
                gtk.idle_add(set_entry_color, textbox, "red")
            else:
                gtk.idle_add(set_entry_color, textbox, "dark green")

# Resort based on the header clicked.
def set_sort_by(header, i):
    t = time.time()
    s = header.get_sort_order()
    if not header.get_sort_indicator() or s == gtk.SORT_DESCENDING:
        s = gtk.SORT_ASCENDING
    else: s = gtk.SORT_DESCENDING
    for h in widgets["songlist"].get_columns():
        h.set_sort_indicator(False)
    header.set_sort_indicator(True)
    header.set_sort_order(s)
    player.playlist.sort_by(HEADERS[i[0]], s == gtk.SORT_DESCENDING)
    print "Sorting songlist took %f seconds " % (time.time() - t)
    refresh_songlist()

# Clear the songlist and readd the songs currently wanted.
def refresh_songlist():
    t = time.time()
    sl = widgets["songlist"]
    sl.set_model(None)
    widgets.songs.clear()
    statusbar = widgets["statusbar"]
    for song in player.playlist:
        if song is CURRENT_SONG[0]:
            widgets.songs.append([song.get(h, "") for h in HEADERS] +
                                  [song, 700])
        else:
            widgets.songs.append([song.get(h, "") for h in HEADERS] +
                                  [song, 400])
    j = statusbar.get_context_id("playlist")
    i = len(list(player.playlist))
    statusbar.push(j, "%d song%s found." % (i, (i != 1 and "s" or "")))
    sl.set_model(widgets.songs)
    print "Setting songlist took %f seconds " % (time.time() - t)

HEADERS = ["=#", "title", "album", "artist"]
HEADERS_FILTER = { "=#": "Track", "tracknumber": "Track" }

FILTER_ALL = lambda x: True
CURRENT_FILTER = [ FILTER_ALL ]
CURRENT_SONG = [ None ]

# Set the color of some text.
def set_entry_color(entry, color):
    layout = entry.get_layout()
    text = layout.get_text()
    markup = '<span foreground="%s">%s</span>' % (color, escape(text))
    layout.set_markup(markup)

# Build a new filter around our list model, set the headers to their
# new values.
def set_column_headers(sl):
    ti = time.time()
    sl.set_model(None)
    widgets.songs = gtk.ListStore(*([str] * len(HEADERS) + [object, int]))
    for c in sl.get_columns(): sl.remove_column(c)
    for i, t in enumerate(HEADERS):
        render = gtk.CellRendererText()
        column = gtk.TreeViewColumn(HEADERS_FILTER.get(t, t).title(),
                                    render, text = i, weight = len(HEADERS)+1)
        column.set_resizable(True)
        column.set_clickable(True)
        column.set_sort_indicator(False)
        column.connect('clicked', set_sort_by, (i,))
        sl.append_column(column)
    refresh_songlist()
    sl.set_model(widgets.songs)
    print "Initting headers took %f seconds " % (time.time() - ti)

widgets = Widgets("quodlibet.glade")

def setup_nonglade():
    # Set up the main song list store.
    sl = widgets["songlist"]
    widgets.songs = gtk.ListStore(object)
    set_column_headers(sl)
    refresh_songlist()

    # Build a model and view for our ComboBoxEntry.
    liststore = gtk.ListStore(str)
    widgets["query"].set_model(liststore)
    cell = gtk.CellRendererText()
    widgets["query"].pack_start(cell, True)
    widgets["query"].add_attribute(cell, 'text', 0)
    widgets["query"].child.connect('activate', text_parse)
    widgets["query"].child.connect('changed', test_filter)
    widgets["search_button"].connect('clicked', text_parse)

    # Initialize volume controls.
    widgets["volume"].set_value(player.device.volume)

    widgets.wrap = GTKSongInfoWrapper()

    widgets["main_window"].show()

    gtk.threads_init()

def main():
    config_fn = os.path.join(os.environ["HOME"], ".quodlibet", "config")
    config.init(config_fn)
    HEADERS[:] = config.get("settings", "headers").split()
    if "title" not in HEADERS: HEADERS.append("title")
    cache_fn = os.path.join(os.environ["HOME"], ".quodlibet", "songs")
    library.load(cache_fn)
    if config.get("settings", "scan"):
        library.scan(config.get("settings", "scan").split(":"))
    player.playlist.set_playlist(library.values())
    player.playlist.sort_by(HEADERS[0])
    setup_nonglade()
    print "Done loading songs."
    t = threading.Thread(target = player.playlist.play,
                         args = (widgets.wrap,))
    gc.collect()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    t.start()
    try: gtk.main()
    except: gtk.main_quit()
    player.playlist.quitting()
    t.join()
    util.mkdir(os.path.join(os.environ["HOME"], ".quodlibet"))
    library.save(cache_fn)
    config.write(file(config_fn, "w"))

if __name__ == "__main__": main()
