"""Static UI contracts for the responsive workflow repair.

These tests run on any platform. They parse the HTML documents, the stylesheet,
and the app script into structure (elements with attributes, CSS rules keyed by
selector and media context, JavaScript function bodies) and assert the contracts
that the browser-facing repair must satisfy: a favicon link, a single
``aria-current="step"`` workflow rail item, an accessibly hidden file input,
non-viewport heading typography, a non-sticky mobile top bar, 44px touch
targets on mobile, and a frame-coalesced workflow rail controller wired to the
functions that change section visibility.
"""

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
APP_DIR = ROOT_DIR / "app"
INDEX_HTML = APP_DIR / "index.html"
LINE_CLEANER_HTML = APP_DIR / "line-cleaner-experiment.html"
STYLES_CSS = APP_DIR / "styles.css"
APP_JS = APP_DIR / "app.js"

MOBILE_MEDIA = "@media (max-width: 760px)"
TABLET_MEDIA = "@media (max-width: 1180px)"

TOUCH_TARGET_SELECTORS = (
    ".primary-button",
    ".magic-button",
    ".ghost-button",
    ".icon-button",
    ".choice-button",
    ".compact-button",
    ".text-button",
    ".small-link",
    'input[type="text"]',
    'input[type="number"]',
    "select",
    ".checkbox-row",
    ".magic-realesrgan-option",
    ".magic-resize-option",
)

VIEWPORT_UNIT_PATTERN = re.compile(r"\d(?:vw|vh|vmin|vmax)\b", re.IGNORECASE)


# --- HTML -------------------------------------------------------------------


class ElementCollector(HTMLParser):
    """Flatten a document into (tag, attrs) tuples in document order."""

    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def parse_elements(path):
    collector = ElementCollector()
    collector.feed(path.read_text(encoding="utf-8"))
    return collector.elements


def classes_of(attrs):
    return (attrs.get("class") or "").split()


def find_elements(elements, tag=None, class_name=None, element_id=None):
    matches = []
    for element_tag, attrs in elements:
        if tag is not None and element_tag != tag:
            continue
        if class_name is not None and class_name not in classes_of(attrs):
            continue
        if element_id is not None and attrs.get("id") != element_id:
            continue
        matches.append((element_tag, attrs))
    return matches


# --- CSS --------------------------------------------------------------------


def strip_css_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def parse_css_rules(text, media=None):
    """Return a flat list of (selector, declarations, media) triples.

    ``declarations`` is a list of (property, value) tuples in source order so
    that repeated properties are preserved. Nested ``@media`` blocks are
    recursed into with their prelude recorded as the ``media`` context.
    """

    rules = []
    index = 0
    length = len(text)
    while index < length:
        open_brace = text.find("{", index)
        if open_brace == -1:
            break
        prelude = text[index:open_brace].strip()
        depth = 1
        cursor = open_brace + 1
        while cursor < length and depth:
            char = text[cursor]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            cursor += 1
        body = text[open_brace + 1 : cursor - 1]
        if prelude.startswith("@media"):
            rules.extend(parse_css_rules(body, media=" ".join(prelude.split())))
        elif prelude.startswith("@"):
            pass
        else:
            declarations = []
            for chunk in body.split(";"):
                if ":" not in chunk:
                    continue
                name, value = chunk.split(":", 1)
                declarations.append((name.strip().lower(), " ".join(value.split())))
            rules.append((prelude, declarations, media))
        index = cursor
    return rules


def selector_parts(selector):
    return [" ".join(part.split()) for part in selector.split(",")]


def rules_for(rules, selector_part, media=None):
    return [
        rule
        for rule in rules
        if rule[2] == media and selector_part in selector_parts(rule[0])
    ]


def declared_value(rules, selector_part, property_name, media=None):
    """Last declared value for a property on rules matching one selector part."""

    value = None
    for _selector, declarations, _media in rules_for(rules, selector_part, media):
        for name, declared in declarations:
            if name == property_name:
                value = declared
    return value


def is_heading_selector(selector):
    return any(re.fullmatch(r"h[1-3]", part) for part in selector_parts(selector))


# --- JavaScript --------------------------------------------------------------


def function_body(source, name):
    """Return the brace-delimited body of ``function <name>(`` or fail."""

    match = re.search(rf"\bfunction\s+{re.escape(name)}\s*\(", source)
    if match is None:
        raise AssertionError(f"function {name} is not defined")
    open_brace = source.find("{", match.end())
    depth = 0
    cursor = open_brace
    while cursor < len(source):
        char = source[cursor]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1 : cursor]
        cursor += 1
    raise AssertionError(f"function {name} body is not brace balanced")


# --- Tests ------------------------------------------------------------------


class FaviconLinkContractTests(unittest.TestCase):
    """Both documents declare the served PNG favicon so the tab shows the product icon."""

    def assert_favicon_link(self, path):
        icons = [
            attrs
            for tag, attrs in find_elements(parse_elements(path), tag="link")
            if "icon" in (attrs.get("rel") or "").split()
        ]
        self.assertEqual(len(icons), 1, f"{path.name} must declare exactly one icon link")
        self.assertEqual(icons[0].get("href"), "/favicon.ico")
        self.assertEqual(icons[0].get("type"), "image/png")

    def test_index_document_links_the_served_favicon(self):
        self.assert_favicon_link(INDEX_HTML)

    def test_line_cleaner_document_links_the_served_favicon(self):
        self.assert_favicon_link(LINE_CLEANER_HTML)


class WorkflowRailMarkupContractTests(unittest.TestCase):
    """The initial rail exposes a single current step: the import section."""

    def test_exactly_one_rail_item_is_current_and_it_is_the_import_step(self):
        rail_items = find_elements(parse_elements(INDEX_HTML), tag="a", class_name="rail-item")
        self.assertEqual(len(rail_items), 4)
        current = [attrs for _tag, attrs in rail_items if attrs.get("aria-current") == "step"]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].get("href"), "#importSection")
        self.assertIn("active", classes_of(current[0]))

    def test_every_rail_item_targets_an_existing_section(self):
        elements = parse_elements(INDEX_HTML)
        ids = {attrs.get("id") for _tag, attrs in elements if attrs.get("id")}
        for _tag, attrs in find_elements(elements, tag="a", class_name="rail-item"):
            with self.subTest(href=attrs.get("href")):
                self.assertTrue(attrs.get("href", "").startswith("#"))
                self.assertIn(attrs["href"][1:], ids)


class UploadInputContractTests(unittest.TestCase):
    """The file input is hidden accessibly but keeps its label wiring and file filters."""

    def test_upload_input_is_visually_hidden_and_keeps_its_attributes(self):
        elements = parse_elements(INDEX_HTML)
        inputs = find_elements(elements, tag="input", element_id="uploadInput")
        self.assertEqual(len(inputs), 1)
        attrs = inputs[0][1]
        self.assertEqual(attrs.get("type"), "file")
        self.assertIn("multiple", attrs)
        self.assertIn("visually-hidden-input", classes_of(attrs))
        self.assertEqual(
            attrs.get("accept"),
            ".mp4,.mov,.mkv,.webm,.gif,.png,.jpg,.jpeg,.webp,.bmp,video/*,image/*",
        )

    def test_dropzone_label_still_targets_the_upload_input(self):
        elements = parse_elements(INDEX_HTML)
        labels = find_elements(elements, tag="label", element_id="uploadDropzone")
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0][1].get("for"), "uploadInput")
        self.assertEqual(labels[0][1].get("aria-controls"), "uploadInput")


class StylesheetContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = parse_css_rules(strip_css_comments(STYLES_CSS.read_text(encoding="utf-8")))

    def test_visually_hidden_input_rule_removes_the_control_from_layout(self):
        expected = {
            "position": "absolute",
            "width": "1px",
            "height": "1px",
            "padding": "0",
            "overflow": "hidden",
            "clip": "rect(0 0 0 0)",
            "clip-path": "inset(50%)",
            "white-space": "nowrap",
            "border": "0",
        }
        matches = rules_for(self.rules, ".visually-hidden-input")
        self.assertEqual(len(matches), 1, "one top-level .visually-hidden-input rule expected")
        declared = dict(matches[0][1])
        for name, value in expected.items():
            with self.subTest(property=name):
                self.assertEqual(declared.get(name), value)

    def test_heading_font_sizes_never_use_viewport_units(self):
        heading_rules = [rule for rule in self.rules if is_heading_selector(rule[0])]
        self.assertTrue(heading_rules, "expected heading rules in the stylesheet")
        for selector, declarations, media in heading_rules:
            for name, value in declarations:
                if name != "font-size":
                    continue
                with self.subTest(selector=selector, media=media, value=value):
                    self.assertIsNone(VIEWPORT_UNIT_PATTERN.search(value))
                    self.assertNotIn("clamp(", value)

    def test_h2_uses_fixed_breakpoint_sizes(self):
        self.assertEqual(declared_value(self.rules, "h2", "font-size"), "34px")
        self.assertEqual(declared_value(self.rules, "h2", "font-size", media=TABLET_MEDIA), "30px")
        self.assertEqual(declared_value(self.rules, "h2", "font-size", media=MOBILE_MEDIA), "26px")

    def test_topbar_is_not_sticky_on_mobile(self):
        self.assertEqual(declared_value(self.rules, ".topbar", "position"), "sticky")
        self.assertEqual(declared_value(self.rules, ".topbar", "position", media=MOBILE_MEDIA), "static")

    def test_source_strip_scrolls_horizontally_on_mobile(self):
        self.assertEqual(declared_value(self.rules, ".source-strip", "display", media=MOBILE_MEDIA), "flex")
        self.assertEqual(declared_value(self.rules, ".source-strip", "overflow-x", media=MOBILE_MEDIA), "auto")
        self.assertEqual(
            declared_value(self.rules, ".source-strip", "overscroll-behavior-x", media=MOBILE_MEDIA),
            "contain",
        )
        self.assertEqual(declared_value(self.rules, ".source-strip > div", "flex", media=MOBILE_MEDIA), "0 0 132px")

    def test_mobile_touch_targets_are_at_least_44px_tall(self):
        for selector in TOUCH_TARGET_SELECTORS:
            with self.subTest(selector=selector):
                self.assertEqual(declared_value(self.rules, selector, "min-height", media=MOBILE_MEDIA), "44px")

    def test_mobile_icon_and_clear_runtime_controls_are_44px_square(self):
        for selector in (".icon-button", ".clear-runtime-button"):
            with self.subTest(selector=selector):
                self.assertEqual(declared_value(self.rules, selector, "width", media=MOBILE_MEDIA), "44px")
                self.assertEqual(declared_value(self.rules, selector, "min-width", media=MOBILE_MEDIA), "44px")
                self.assertEqual(declared_value(self.rules, selector, "min-height", media=MOBILE_MEDIA), "44px")


class WorkflowRailControllerContractTests(unittest.TestCase):
    """No DOM runtime is available here, so the controller is checked at source level.

    Contract: ``syncWorkflowRail`` derives the active rail item from visible
    sections and scroll position; ``scheduleWorkflowRailSync`` coalesces calls
    into one animation frame; it is bound to DOM ready, scroll, and resize; and
    every function that changes section visibility schedules a sync.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")

    def test_sync_workflow_rail_toggles_active_class_and_aria_current(self):
        body = function_body(self.source, "syncWorkflowRail")
        self.assertIn('querySelectorAll(".rail-item")', body)
        self.assertIn('classList.toggle("active"', body)
        self.assertIn('setAttribute("aria-current", "step")', body)
        self.assertIn('removeAttribute("aria-current")', body)
        self.assertIn("offsetParent !== null", body)

    def test_schedule_workflow_rail_sync_coalesces_into_one_animation_frame(self):
        body = function_body(self.source, "scheduleWorkflowRailSync")
        self.assertIn("requestAnimationFrame", body)
        self.assertIn("syncWorkflowRail()", body)
        self.assertRegex(self.source, r"\blet\s+workflowRailFrame\s*=\s*null\s*;")

    def test_controller_is_bound_to_dom_ready_scroll_and_resize(self):
        dom_ready = re.search(
            r'addEventListener\("DOMContentLoaded",\s*\(\)\s*=>\s*\{',
            self.source,
        )
        self.assertIsNotNone(dom_ready)
        open_brace = self.source.find("{", dom_ready.end() - 1)
        depth = 0
        cursor = open_brace
        while cursor < len(self.source):
            if self.source[cursor] == "{":
                depth += 1
            elif self.source[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        ready_body = self.source[open_brace : cursor + 1]
        self.assertIn("scheduleWorkflowRailSync()", ready_body)
        self.assertRegex(
            self.source,
            r'addEventListener\("scroll",\s*scheduleWorkflowRailSync,\s*\{\s*passive:\s*true\s*\}\)',
        )
        self.assertRegex(self.source, r'addEventListener\("resize",\s*scheduleWorkflowRailSync\)')

    def test_section_visibility_changes_schedule_a_rail_sync(self):
        for name in ("showAnimationWorkbench", "applyUpload", "renderJob"):
            with self.subTest(function=name):
                self.assertIn("scheduleWorkflowRailSync()", function_body(self.source, name))


if __name__ == "__main__":
    unittest.main()
