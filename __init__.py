import os
import re
from aqt import mw
from aqt import gui_hooks
from aqt.qt import QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, Qt, QShortcut, QKeySequence, QPushButton, \
    QSize, QObject, QEvent
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
from PyQt6.QtCore import QUrl, QByteArray
from PyQt6.QtGui import QIcon

# Global references to maintain program states across windows
_current_browser_ref = None
_is_page_loaded = False
_current_question_text = ""
_prompt_input_ref = None  # Global reference to read the custom prompt field safely


def clean_html(raw_html):
    """Removes HTML styling blocks, maps layout breaks to clean newlines, and strips text tags."""
    if not raw_html:
        return ""

    # 1. Strip out the entire <style>...</style> block along with everything inside it
    html_no_css = re.sub(r'<style[^>]*>([\s\S]*?)</style>', '', raw_html, flags=re.IGNORECASE)

    # 2. Convert structural spacing tags into actual clean newlines so MCQs format nicely
    html_with_newlines = re.sub(r'<(br|div|p)[^>]*>', '\n', html_no_css, flags=re.IGNORECASE)

    # 3. Strip out all remaining layout text tags cleanly
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', html_with_newlines)

    # 4. Collapse running double newlines down to clean single/double spaces
    cleantext = re.sub(r'\n\s*\n', '\n', cleantext)

    return cleantext.strip()


def send_entire_question_to_gemini():
    """Triggered by the button or shortcut. Ensures browser is open and pushes text."""
    global _current_question_text, _prompt_input_ref

    # 1. Open the browser panel if it isn't visible
    open_side_browser()

    # 2. Read the custom prompt layout state if it exists
    final_payload = _current_question_text
    if _prompt_input_ref:
        custom_prompt = _prompt_input_ref.text().strip()
        if custom_prompt:
            # Combine the custom instructions with the clean question text blocks
            final_payload = f"{custom_prompt}\n\n{_current_question_text}"

    # 3. If the page is already fully loaded, submit the payload immediately
    if _is_page_loaded and final_payload:
        execute_gemini_submission(final_payload)


def execute_gemini_submission(text_to_send):
    global _current_browser_ref
    if not _current_browser_ref or not text_to_send:
        return

    js_safe_text = repr(text_to_send)

    # This loop polls Gemini's web application layout up to 10 seconds waiting for the box to be editable
    js_code = f"""
    (function() {{
        var attempts = 0;
        var checkExist = setInterval(function() {{
            var inputDiv = document.querySelector('div[contenteditable="true"]');
            attempts++;

            if (inputDiv) {{
                clearInterval(checkExist);
                inputDiv.focus();
                document.execCommand('insertText', false, {js_safe_text});

                // Allow a small pause for Gemini to absorb the pasted string, then trigger submission click
                setTimeout(function() {{
                    var sendButton = document.querySelector('button[aria-label*="Send"], button[mattooltip*="Send"]');
                    if (sendButton && !sendButton.disabled) {{
                        sendButton.click();
                    }}
                }}, 150);
            }}

            if (attempts > 100) {{ // Stop searching after 10 seconds to save performance
                clearInterval(checkExist);
            }}
        }}, 100);
    }})();
    """
    _current_browser_ref.page().runJavaScript(js_code)


def open_side_browser():
    global _current_browser_ref, _is_page_loaded, _current_question_text, _prompt_input_ref

    if hasattr(mw, "my_side_browser") and mw.my_side_browser is not None:
        mw.my_side_browser.show()
        return

    dock = QDockWidget("Gemini Side Assistant", mw)
    dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)

    container = QWidget()
    main_layout = QVBoxLayout()

    # NEW FEATURE: Add Custom Prompt Text Entry Field above the URL window
    _prompt_input_ref = QLineEdit()
    _prompt_input_ref.setPlaceholderText("Optional: Type custom prompt here (e.g., Explain this simply)...")
    _prompt_input_ref.setStyleSheet("padding: 4px; border: 1px solid #c4c7c5; border-radius: 4px; margin-bottom: 2px;")
    main_layout.addWidget(_prompt_input_ref)

    # Address Bar Layout
    nav_layout = QHBoxLayout()
    address_bar = QLineEdit()
    address_bar.setPlaceholderText("Type URL here and press Enter...")
    nav_layout.addWidget(address_bar)

    profile_name = "anki_gemini_browser"
    storage_profile = QWebEngineProfile(profile_name, mw)
    storage_profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)

    browser_page = QWebEnginePage(storage_profile, mw)
    browser = QWebEngineView()
    browser.setPage(browser_page)

    _is_page_loaded = False
    browser.setUrl(QUrl("https://gemini.google.com"))

    _current_browser_ref = browser

    # Signals handling browser load completion state
    def on_load_finished(ok):
        global _is_page_loaded, _current_question_text, _prompt_input_ref
        if ok:
            _is_page_loaded = True

            # Recalculate full payload if user clicked loading trigger
            final_payload = _current_question_text
            if _prompt_input_ref:
                custom_prompt = _prompt_input_ref.text().strip()
                if custom_prompt:
                    final_payload = f"{custom_prompt}\n\n{_current_question_text}"

            if final_payload:
                execute_gemini_submission(final_payload)

    browser.loadFinished.connect(on_load_finished)

    def navigate_to_url():
        global _is_page_loaded
        _is_page_loaded = False
        text = address_bar.text().strip()
        if not text.startswith("http://") and not text.startswith("https://"):
            url_string = f"https://{text}"
        else:
            url_string = text
        browser.setUrl(QUrl(url_string))

    address_bar.returnPressed.connect(navigate_to_url)

    def update_address_bar(url):
        address_bar.setText(url.toString())

    browser.urlChanged.connect(update_address_bar)

    main_layout.addLayout(nav_layout)
    main_layout.addWidget(browser)
    container.setLayout(main_layout)
    dock.setWidget(container)

    mw.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
    mw.my_side_browser = dock
    dock.show()


# Connect the global fallback shortcut key loop
shortcut = QShortcut(QKeySequence("Ctrl+Shift+G"), mw)
shortcut.activated.connect(send_entire_question_to_gemini)

action = mw.form.menuTools.addAction("Open Gemini Panel")
action.triggered.connect(open_side_browser)


# --- EVENT FILTER & RUNTIME BUTTON HOOK ---

class ResizeFilter(QObject):
    def __init__(self, target_web, button):
        super().__init__(target_web)
        self.target_web = target_web
        self.button = button

    def eventFilter(self, obj, event):
        if obj == self.target_web and event.type() == QEvent.Type.Resize:
            self.reposition()
        return super().eventFilter(obj, event)

    def reposition(self):
        if self.button and self.target_web:
            self.button.move(self.target_web.width() - 55, self.target_web.height() - 55)


def inject_gemini_button(card):
    global _current_question_text
    reviewer = mw.reviewer
    if not reviewer or not reviewer.web:
        return

    # Cache the target plain question string safely from Anki's database core
    _current_question_text = clean_html(card.question())

    if hasattr(reviewer, "gemini_floating_btn") and reviewer.gemini_floating_btn:
        try:
            reviewer.gemini_floating_btn.deleteLater()
        except:
            pass

    btn = QPushButton(reviewer.web)
    btn.setFixedSize(QSize(46, 46))
    btn.setToolTip("Send entire question to Gemini (Ctrl+Shift+G)")

    # Resolve the directory paths securely for a custom .webp asset
    addon_dir = os.path.dirname(__file__)
    icon_path = os.path.join(addon_dir, "icon.webp")

    has_icon = os.path.exists(icon_path)

    if has_icon:
        btn.setIcon(QIcon(icon_path))
        btn.setIconSize(QSize(46, 46))
        btn.setText("")
    else:
        print(f"Gemini Addon Warning: Looking for icon at {icon_path} but couldn't find it.")
        btn.setText("G")

    style_text_layer = "font-weight: bold; font-size: 20px; color: #1a73e8;" if not has_icon else ""

    btn.setStyleSheet(f"""
        QPushButton {{
            {style_text_layer}
            border-radius: 23px;
            border: 1px solid #dcdcdc;
            background-color: #ffffff;
            border-bottom: 2px solid #b5b5b5;
        }}
        QPushButton:hover {{
            background-color: #f7f9fa;
            border-color: #1a73e8;
            border-bottom: 2px solid #1557b0;
        }}
        QPushButton:pressed {{
            background-color: #edf2f7;
            border-bottom: 1px solid #cbd5e1;
        }}
    """)

    reviewer.gemini_resize_filter = ResizeFilter(reviewer.web, btn)
    reviewer.web.installEventFilter(reviewer.gemini_resize_filter)

    reviewer.gemini_resize_filter.reposition()

    btn.clicked.connect(send_entire_question_to_gemini)
    reviewer.gemini_floating_btn = btn
    btn.show()


gui_hooks.reviewer_did_show_question.append(inject_gemini_button)