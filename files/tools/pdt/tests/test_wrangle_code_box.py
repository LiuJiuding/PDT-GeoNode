"""`WR-41`/`WR-42`: structural guards for the Code box's line numbers and its Enter key.

    blender.exe -b --factory-startup --python-exit-code 1 --python tools/pdt/tests/test_wrangle_code_box.py

Why structural and not behavioural: the text box cannot be driven headlessly. Measured
(2026-10-09): `Window.event_simulate()` delivers key events and the C++ query proves where the
simulated pointer is, but a simulated `LEFTMOUSE` never puts the widget into its editing state, so
neither the keys nor the pixels can be exercised from a script. The two halves therefore live where
they can be checked:

* the **rule** (which row gets which number) is a Blender-free function in `pdt_wrangle_edit.hh`
  and is asserted in `wrangle_engine_selftest.cpp` (behaviour, not structure);
* the **wiring** is what this file guards - the gutter's three use sites (wrap width, drawing,
  mouse mapping), the theme-colour lookups, and the `Enter`/`Ctrl+Enter` branch with its negative
  controls. Those are the parts a later edit is most likely to break silently, and the eye check
  (`qa/wrangle/code_gutter_shot.py`, `qa/wrangle/code_shortcuts_checklist.md`) covers the rest.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TESTS = REPO / "tools" / "pdt" / "tests"
UI = REPO / "source" / "blender" / "editors" / "interface"
NODE = REPO / "source" / "blender" / "nodes" / "geometry" / "nodes" / "custom"

CHECKS = 0
FAILURES: list[str] = []


def check(label, actual, expected):
    global CHECKS
    CHECKS += 1
    ok = actual == expected
    print(f"{'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {actual!r}, want {expected!r}"))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    check(label, bool(condition), True)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def body_of(text: str, signature: str) -> str:
    """The text of one function: from its signature to the closing brace in column 0."""
    start = text.index(signature)
    end = text.index("\n}\n", start)
    return text[start:end]


def main():
    layout_hh = read(REPO / "source/blender/editors/include/UI_interface_layout.hh")
    layout_cc = read(UI / "interface_layout.cc")
    textbox_cc = read(UI / "buttons" / "interface_textbox.cc")
    widgets_cc = read(UI / "interface_widgets.cc")
    handlers_cc = read(UI / "interface_handlers.cc")
    node_cc = read(NODE / "node_geo_pdt_wrangle.cc")
    intern_hh = read(UI / "interface_intern.hh")

    # ---------------------------------------------------------------- the switch is the box's own
    check_true("WR-41 the text box carries its own `line_numbers` flag",
               "bool line_numbers = false;" in intern_hh)
    check_true("WR-41 the layout API takes it, defaulted off for every existing caller",
               layout_hh.count("const bool line_numbers = false)") == 2)
    check_true("WR-41 and it is set on the text box, not on the base button",
               "textbox->line_numbers = line_numbers;" in layout_cc)
    check_true("WR-41 only the wrangle node's Code box asks for it",
               node_cc.count("line_numbers") == 0 and
               "error_position.second,\n                        true);" in node_cc)

    # ------------------------------------------------------- the band is not part of the text
    check_true("WR-41 the wrap width subtracts the band (beyond the field's own padding)",
               "textbox_line_number_inset(textbox) - button_text_padding(textbox)" in textbox_cc)
    check_true("WR-41 the mouse mapping measures from the widget's edge too",
               "textbox->rect.xmin - button_text_padding(textbox) +\n"
               "                      textbox_line_number_inset(textbox)" in textbox_cc)
    check_true("WR-41 the drawer puts the text one inset past the widget's left edge",
               "rect.xmin = band_left + line_number_inset;" in widgets_cc)
    check_true("WR-41 the band hugs the widget's left edge and stops one digit short of the code",
               "const int band_left = rect.xmin - text_padding;" in widgets_cc and
               "const int band_right = band_left + line_number_inset - digit_width;" in widgets_cc)
    check_true("WR-41 all three use sites come from one inset function",
               textbox_cc.count("textbox_line_number_inset(textbox)") == 2 and
               widgets_cc.count("textbox_line_number_inset(textbox)") == 1)

    # ------------------------------------------------------------------ the colours are the theme's
    gutter = body_of(widgets_cc, "  const int line_number_inset = textbox_line_number_inset(textbox);")
    for name, token in (("background band", "TH_GRID"),
                        ("numbers", "TH_LINENUMBERS"),
                        ("cursor line", "TH_HILITE")):
        check_true(f"WR-41 the {name} colour comes from the Text theme space",
                   token in gutter and "theme::get_color_ptr(theme::theme_get(), SPACE_TEXT" in gutter)
    check_true("WR-41 and no colour is written into the drawing code",
               "immUniformColor3f(" not in gutter and "immUniformColor4f(" not in gutter)

    # -------------------------------------------------------------- WR-42: the Enter key's branch
    enter = handlers_cc[handlers_cc.index("      case EVT_PADENTER:") :]
    enter = enter[: enter.index("      case EVT_DELKEY:")]
    check_true("WR-42 the Enter case asks whether this is the Code box",
               "textedit_is_wrangle_code_box(but, data)" in enter)
    check_true("WR-42 Ctrl+Enter writes the code back through the exit path's own function",
               "apply_but_TEX(C, but, data);" in enter)
    check_true("WR-42 plain Enter and Shift+Enter insert a newline",
               'char utf8_buf[2] = "\\n";' in enter)
    check_true("WR-42 the write-back must not mark the edit as already applied, "
               "or leaving would drop what was typed after Ctrl+Enter",
               "applied_interactive" not in enter)
    check_true("WR-42 every other text box keeps Blender's own rule (Enter confirms)",
               "button_activate_state(C, but, BUTTON_STATE_EXIT);" in enter and
               'char utf8_buf[2] = "\\n";' in enter.split("button_activate_state")[0].split("textedit_is_wrangle_code_box")[-1])

    # The predicate itself is the same one the shortcuts use, and it is the syntax-highlight flag -
    # i.e. the box, not the node type.
    predicate = body_of(handlers_cc, "static bool textedit_is_wrangle_code_box(")
    check_true("WR-41/42 the box is recognised by its own flag, like the shortcuts do",
               "syntax_highlight" in predicate and "ButtonType::TextBox" in predicate)

    print(f"== test_wrangle_code_box: {CHECKS} checks, {len(FAILURES)} failure(s) ==")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

