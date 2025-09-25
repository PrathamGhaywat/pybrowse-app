import sys
import os
import sqlite3
from datetime import datetime
from PyQt6.QtCore import *
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import *
from PyQt6.QtWebEngineWidgets import *
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor, QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel

def resource_path(relative_path):
    """Get absolute path for dev and for PyInstaller bundle"""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        return os.path.join(sys._MEIPASS, relative_path)  # type: ignore
    return os.path.join(os.path.abspath("."), relative_path)

class PasswordManager(QObject):
    """Handle password detection, saving, and auto-fill functionality"""
    
    def __init__(self, browser):
        super().__init__()
        self.browser = browser
        self.pending_save_data = {}  # Store data for save prompts
    
    def inject_password_detection_script(self, page):
        """Inject JavaScript to detect login forms and handle password saving"""
        
        password_script = """
        (function() {
            // Password manager detection script
            let passwordForms = [];
            let isFormSubmitted = false;
            
            function detectPasswordForms() {
                const forms = document.querySelectorAll('form');
                passwordForms = [];
                
                forms.forEach(form => {
                    const passwordInputs = form.querySelectorAll('input[type="password"]');
                    const usernameInputs = form.querySelectorAll('input[type="text"], input[type="email"], input[name*="user"], input[name*="email"], input[name*="login"]');
                    
                    if (passwordInputs.length > 0 && usernameInputs.length > 0) {
                        passwordForms.push({
                            form: form,
                            usernameField: usernameInputs[0],
                            passwordField: passwordInputs[0]
                        });
                        
                        // Add form submission listener
                        form.addEventListener('submit', function(e) {
                            const username = usernameInputs[0].value;
                            const password = passwordInputs[0].value;
                            
                            if (username && password) {
                                isFormSubmitted = true;
                                // Signal to Python that we want to save password
                                window.pendingPasswordSave = {
                                    url: window.location.href,
                                    domain: window.location.hostname,
                                    username: username,
                                    password: password
                                };
                                console.log('Password form submitted, ready to save');
                            }
                        });
                    }
                });
                
                return passwordForms.length > 0;
            }
            
            // Auto-fill function
            window.autoFillPassword = function(username, password) {
                passwordForms.forEach(formData => {
                    if (formData.usernameField && formData.passwordField) {
                        formData.usernameField.value = username;
                        formData.passwordField.value = password;
                        
                        // Trigger change events
                        formData.usernameField.dispatchEvent(new Event('input', {bubbles: true}));
                        formData.passwordField.dispatchEvent(new Event('input', {bubbles: true}));
                    }
                });
            };
            
            // Check for forms when DOM is ready
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', detectPasswordForms);
            } else {
                detectPasswordForms();
            }
            
            // Also check periodically for dynamically added forms
            setInterval(detectPasswordForms, 2000);
            
            // Mark that password manager is active
            window.passwordManagerActive = true;
            
        })();
        """
        
        page.runJavaScript(password_script)
    
    def check_for_password_save(self, page):
        """Check if there's a pending password save request"""
        # Safety check: ensure page is still valid
        try:
            if not page:
                return
                
            def handle_password_data(result):
                if result and isinstance(result, dict):
                    url = result.get('url', '')
                    domain = result.get('domain', '')
                    username = result.get('username', '')
                    password = result.get('password', '')
                    
                    if url and username and password:
                        self.prompt_save_password(url, domain, username, password)
                        # Clear the pending save
                        try:
                            page.runJavaScript("window.pendingPasswordSave = null;")
                        except RuntimeError:
                            pass  # Page was deleted, ignore
            
            page.runJavaScript("window.pendingPasswordSave", handle_password_data)
        except RuntimeError:
            # Page was deleted, stop checking
            pass
    
    def prompt_save_password(self, url, domain, username, password):
        """Show dialog to ask user if they want to save the password"""
        
        # Create a custom dialog
        dialog = QDialog(self.browser)
        dialog.setWindowTitle("💾 Save Password")
        dialog.setFixedSize(400, 200)
        
        layout = QVBoxLayout(dialog)
        
        # Message
        message = QLabel(f"Save password for {username} on {domain}?")
        message.setWordWrap(True)
        layout.addWidget(message)
        
        # Username display
        username_label = QLabel(f"Username: {username}")
        layout.addWidget(username_label)
        
        # Password display (masked)
        password_label = QLabel(f"Password: {'•' * len(password)}")
        layout.addWidget(password_label)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        save_btn = QPushButton("💾 Save")
        save_btn.clicked.connect(lambda: self.save_password_and_close(dialog, url, domain, username, password))
        button_layout.addWidget(save_btn)
        
        never_btn = QPushButton("❌ Never for this site")
        never_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(never_btn)
        
        not_now_btn = QPushButton("⏭️ Not now")
        not_now_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(not_now_btn)
        
        layout.addLayout(button_layout)
        
        # Show dialog
        dialog.exec()
    
    def save_password_and_close(self, dialog, url, domain, username, password):
        """Save the password and close the dialog"""
        success = self.browser.db.save_password(url, domain, username, password)
        if success:
            print(f"Password saved for {username} on {domain}")
        dialog.accept()
    
    def auto_fill_passwords(self, page, domain):
        """Auto-fill saved passwords for the current domain"""
        
        saved_passwords = self.browser.db.get_saved_passwords(domain)
        
        if saved_passwords:
            # Use the most recently used password
            url, domain, username, password, last_used = saved_passwords[0]
            
            # Inject auto-fill JavaScript
            autofill_script = f"""
            if (window.autoFillPassword) {{
                window.autoFillPassword('{username}', '{password}');
            }}
            """
            # The bridge between python and js is crazy lol
            page.runJavaScript(autofill_script)
            print(f"Auto-filled password for {username} on {domain}")

class BrowserDatabase:
    """Handle SQLite database operations for browser history and sessions"""
    
    def __init__(self, db_path="pybrowse.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT,
                visit_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                visit_count INTEGER DEFAULT 1
            )
        ''')
        
        # Create sessions table for tab restoration
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_name TEXT DEFAULT 'default',
                tab_index INTEGER,
                url TEXT NOT NULL,
                title TEXT,
                is_current_tab BOOLEAN DEFAULT 0,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create passwords table for password manager
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS passwords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                domain TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(url, username)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_to_history(self, url, title=""):
        """Add a URL to browsing history"""
        if not url or url.startswith('file://'):
            return  # Don't save local files or empty URLs
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if URL already exists
        cursor.execute('SELECT id, visit_count FROM history WHERE url = ?', (url,))
        existing = cursor.fetchone()
        
        if existing:
            # Update existing entry
            cursor.execute('''
                UPDATE history 
                SET visit_count = visit_count + 1, 
                    visit_time = CURRENT_TIMESTAMP,
                    title = ?
                WHERE id = ?
            ''', (title, existing[0]))
        else:
            # Insert new entry
            cursor.execute('''
                INSERT INTO history (url, title, visit_count) 
                VALUES (?, ?, 1)
            ''', (url, title))
        
        conn.commit()
        conn.close()
    
    def get_history(self, limit=100):
        """Get browsing history ordered by most recent"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT url, title, visit_time, visit_count 
            FROM history 
            ORDER BY visit_time DESC 
            LIMIT ?
        ''', (limit,))
        
        history = cursor.fetchall()
        conn.close()
        return history
    
    def save_session(self, tabs_data):
        """Save current session (open tabs)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Clear existing session
        cursor.execute('DELETE FROM sessions WHERE session_name = ?', ('default',))
        
        # Save current tabs
        for i, (url, title, is_current) in enumerate(tabs_data):
            cursor.execute('''
                INSERT INTO sessions (session_name, tab_index, url, title, is_current_tab)
                VALUES (?, ?, ?, ?, ?)
            ''', ('default', i, url, title, is_current))
        
        conn.commit()
        conn.close()
    
    def restore_session(self):
        """Restore previous session tabs"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT tab_index, url, title, is_current_tab 
            FROM sessions 
            WHERE session_name = 'default'
            ORDER BY tab_index
        ''')
        
        session_data = cursor.fetchall()
        conn.close()
        return session_data
    
    def clear_history(self):
        """Clear all browsing history"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM history')
        conn.commit()
        conn.close()
    
    def save_password(self, url, domain, username, password):
        """Save or update a password"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO passwords (url, domain, username, password, last_used)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (url, domain, username, password))
            conn.commit()
            return True
        except Exception as e:
            print(f"Error saving password: {e}")
            return False
        finally:
            conn.close()
    
    def get_saved_passwords(self, domain=None):
        """Get saved passwords, optionally filtered by domain"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if domain:
            cursor.execute('''
                SELECT url, domain, username, password, last_used 
                FROM passwords 
                WHERE domain = ? 
                ORDER BY last_used DESC
            ''', (domain,))
        else:
            cursor.execute('''
                SELECT url, domain, username, password, last_used 
                FROM passwords 
                ORDER BY last_used DESC
            ''')
        
        passwords = cursor.fetchall()
        conn.close()
        return passwords
    
    def delete_password(self, url, username):
        """Delete a specific password"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM passwords WHERE url = ? AND username = ?', (url, username))
        conn.commit()
        conn.close()
    
    def clear_all_passwords(self):
        """Clear all saved passwords"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM passwords')
        conn.commit()
        conn.close()



class SearchInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, browser):
        super().__init__()
        self.browser = browser

    def interceptRequest(self, info):
        url = info.requestUrl().toString()
        
        # Debug: Uncomment to see all requests (for troubleshooting)
        # print(f"Request: {url}")
        
        # Only intercept Google searches from our home page and redirect to selected engine
        if "google.com/search?q=" in url and hasattr(self.browser, 'search_engine_combo'):
            current_engine = self.browser.search_engine_combo.currentText()
            if current_engine != "Google":
                # Extract the search query
                import urllib.parse
                parsed = urllib.parse.urlparse(url)
                query_params = urllib.parse.parse_qs(parsed.query)
                if 'q' in query_params:
                    search_query = query_params['q'][0]
                    new_url = self.browser.get_search_url(search_query, current_engine)
                    info.redirect(QUrl(new_url))
                    return
        
        # Block ads (currently disabled - no blocked domains)
        if self.is_ad(url):
            info.block(True)

    def is_ad(self, url):
        #domains = open("ads.txt", "r")
        blocked_domains = [
            #domains
        ]

        for domain in blocked_domains:
            if domain in url:
                return True
        return False


class Browser(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Set modern window title and properties
        self.setWindowTitle("🚀 PyBrowse - Privacy-First Browser")
        self.setMinimumSize(1200, 800)

        # Initialize database
        self.db = BrowserDatabase()
        
        # Initialize password manager
        self.password_manager = PasswordManager(self)
        
        self.search_interceptor = SearchInterceptor(self)

        # Initialize tab management for sidebar layout
        self.tab_buttons = []
        self.current_tab_index = 0

        # Create main layout with sidebar
        self.setup_modern_layout()

        # Apply modern styling
        self.apply_modern_styling()
        
        # Restore previous session or create new tab
        self.restore_previous_session()

        self.show()

    def setup_modern_layout(self):
        """Create Arc/Zen browser inspired layout with vertical sidebar"""
        # Create main widget and layout
        main_widget = QWidget()
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Create sidebar for tabs
        self.setup_sidebar()
        
        # Create main content area
        self.setup_content_area()
        
        # Add sidebar and content to main layout
        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.content_area, 1)  # Give content area more space
        
        self.setCentralWidget(main_widget)
    
    def setup_sidebar(self):
        """Create the left sidebar with vertical tabs"""
        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(250)
        self.sidebar.setObjectName("sidebar")
        
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(8, 8, 8, 8)
        sidebar_layout.setSpacing(4)
        
        # Add browser logo/title
        logo_label = QLabel("PyBrowse")
        logo_label.setObjectName("logo")
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(logo_label)
        
        # Add new tab button
        self.new_tab_btn = QPushButton("+ New Tab")
        self.new_tab_btn.setObjectName("newTabBtn")
        self.new_tab_btn.clicked.connect(lambda: self.add_new_tab())
        sidebar_layout.addWidget(self.new_tab_btn)
        
        # Create scroll area for tabs
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # Tab container widget
        self.tab_container = QWidget()
        self.tab_layout = QVBoxLayout(self.tab_container)
        self.tab_layout.setContentsMargins(0, 0, 0, 0)
        self.tab_layout.setSpacing(2)
        self.tab_layout.addStretch()  # Push tabs to top
        
        scroll_area.setWidget(self.tab_container)
        sidebar_layout.addWidget(scroll_area, 1)
        
        # Add settings and controls at bottom
        self.setup_sidebar_controls(sidebar_layout)
        
        # Store tab widgets for management
        self.tab_widgets = []
        self.current_tab_index = 0
    
    def setup_sidebar_controls(self, layout):
        """Add navigation controls to sidebar bottom"""
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)
        
        # Navigation buttons row
        nav_widget = QWidget()
        nav_layout = QHBoxLayout(nav_widget)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(4)
        
        self.back_btn = QPushButton("◀")
        self.back_btn.setFixedSize(32, 32)
        self.back_btn.clicked.connect(self.navigate_back)
        nav_layout.addWidget(self.back_btn)
        
        self.forward_btn = QPushButton("▶")
        self.forward_btn.setFixedSize(32, 32)
        self.forward_btn.clicked.connect(self.navigate_forward)
        nav_layout.addWidget(self.forward_btn)
        
        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setFixedSize(32, 32)
        self.refresh_btn.clicked.connect(self.navigate_reload)
        nav_layout.addWidget(self.refresh_btn)
        
        self.home_btn = QPushButton("🏠")
        self.home_btn.setFixedSize(32, 32)
        self.home_btn.clicked.connect(self.navigate_home)
        nav_layout.addWidget(self.home_btn)
        
        nav_layout.addStretch()
        controls_layout.addWidget(nav_widget)
        
        # URL bar
        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("Search or enter address...")
        self.url_bar.returnPressed.connect(self.navigate_to_url)
        controls_layout.addWidget(self.url_bar)
        
        # Search engine selector
        self.search_engine_combo = QComboBox()
        self.search_engine_combo.addItems(["Google", "Bing", "DuckDuckGo", "Brave", "Ecosia"])
        self.search_engine_combo.setCurrentText("Google")
        controls_layout.addWidget(self.search_engine_combo)
        
        # Action buttons
        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)
        
        history_btn = QPushButton("📚")
        history_btn.setFixedSize(32, 32)
        history_btn.setToolTip("History")
        history_btn.clicked.connect(self.show_history)
        actions_layout.addWidget(history_btn)
        
        password_btn = QPushButton("🔐")
        password_btn.setFixedSize(32, 32)
        password_btn.setToolTip("Passwords")
        password_btn.clicked.connect(self.show_password_manager)
        actions_layout.addWidget(password_btn)
        
        actions_layout.addStretch()
        controls_layout.addWidget(actions_widget)
        
        layout.addWidget(controls_widget)
    
    def setup_content_area(self):
        """Create the main content area for web pages"""
        self.content_area = QStackedWidget()
        self.content_area.setObjectName("contentArea")
        
        # This will hold all the QWebEngineView widgets
        self.web_views = []

    def add_new_tab(self, qurl=None, label="Blank"):
        if qurl is None:
            html_file = resource_path("pybrowse_home.html")
            qurl = QUrl.fromLocalFile(html_file)

        browser = QWebEngineView()
        browser.setUrl(qurl)
        
        # Set up search interceptor and security settings if page and profile are available
        page = browser.page()
        if page:
            profile = page.profile()
            if profile:
                profile.setUrlRequestInterceptor(self.search_interceptor)
                
                # Configure security settings to reduce console warnings (but allow normal browsing)
                settings = profile.settings()
                if settings:
                    # Allow JavaScript and normal navigation - only restrict dangerous features
                    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)  # Ensure JS is enabled
                    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)  # Allow for normal navigation
                    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)  # Keep restricted
                    settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)  # Keep secure
                    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)  # Keep secure
                    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)  # Only for our home page
                    settings.setAttribute(QWebEngineSettings.WebAttribute.LinksIncludedInFocusChain, True)  # Ensure links work
                    settings.setAttribute(QWebEngineSettings.WebAttribute.SpatialNavigationEnabled, True)  # Better navigation
                    
            # Set up console message filtering
            page.javaScriptConsoleMessage = self.filter_console_messages

        # Add to content area
        tab_index = self.content_area.addWidget(browser)
        self.web_views.append(browser)
        
        # Create tab button in sidebar
        self.create_tab_button(browser, label, tab_index)
        
        # Set as current tab
        self.set_current_tab(tab_index)

        browser.urlChanged.connect(lambda qurl, browser=browser:
                                   self.update_urlbar(qurl, browser))

        browser.loadFinished.connect(lambda success, tab_index=tab_index, browser=browser:
                                     self.on_page_load_finished(success, tab_index, browser))
                                     
        # Track page loads for history
        browser.loadFinished.connect(lambda success, browser=browser:
                                    self.add_to_history(browser) if success else None)
                                     
        # Also update tab title when URL changes (for immediate feedback)
        browser.urlChanged.connect(lambda qurl, browser=browser, tab_index=tab_index:
                                  self.update_tab_title_on_url_change(tab_index, browser, qurl))
                                  
        # Update tab title when icon changes (real favicon loaded)
        browser.iconChanged.connect(lambda icon, tab_index=tab_index, browser=browser:
                                   self.update_tab_title(tab_index, browser))
                                   
        # Update tab title when page title changes  
        browser.titleChanged.connect(lambda title, tab_index=tab_index, browser=browser:
                                    self.update_tab_title(tab_index, browser))

    def create_tab_button(self, browser, label, tab_index):
        """Create a tab button in the sidebar"""
        tab_button = QPushButton(label)
        tab_button.setObjectName("tabButton")
        tab_button.setCheckable(True)
        tab_button.clicked.connect(lambda: self.set_current_tab(tab_index))
        
        # Add close button
        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(lambda: self.close_tab(tab_index))
        
        # Create container for tab button and close button
        tab_widget = QWidget()
        tab_layout = QHBoxLayout(tab_widget)
        tab_layout.setContentsMargins(4, 2, 4, 2)
        tab_layout.addWidget(tab_button, 1)
        tab_layout.addWidget(close_btn)
        
        # Insert before the stretch at the end
        self.tab_layout.insertWidget(self.tab_layout.count() - 1, tab_widget)
        
        # Store the tab widget for updates
        self.tab_widgets.append({
            'widget': tab_widget,
            'button': tab_button,
            'close_btn': close_btn,
            'browser': browser,
            'index': tab_index
        })
    
    def set_current_tab(self, tab_index):
        """Switch to the specified tab"""
        if 0 <= tab_index < len(self.web_views):
            self.content_area.setCurrentIndex(tab_index)
            self.current_tab_index = tab_index
            
            # Update button states
            for i, tab_info in enumerate(self.tab_widgets):
                tab_info['button'].setChecked(i == tab_index)
            
            # Update URL bar
            current_browser = self.web_views[tab_index]
            if current_browser:
                self.url_bar.setText(current_browser.url().toString())
    


    def apply_modern_styling(self):
        # Arc/Zen browser inspired styling with sidebar
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0A0A0A;
                color: #ffffff;
            }
            
            /* Sidebar Styling */
            QWidget#sidebar {
                background-color: #1C1C1E;
                border-right: 1px solid #2C2C2C;
            }
            
            QLabel#logo {
                font-size: 18px;
                font-weight: bold;
                color: #007AFF;
                padding: 12px 0px;
                background: transparent;
            }
            
            /* New Tab Button */
            QPushButton#newTabBtn {
                background-color: #007AFF;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 500;
                font-size: 13px;
            }
            
            QPushButton#newTabBtn:hover {
                background-color: #0056CC;
            }
            
            QPushButton#newTabBtn:pressed {
                background-color: #003D99;
            }
            
            /* Tab Buttons in Sidebar */
            QPushButton#tabButton {
                background-color: transparent;
                color: #E5E5E7;
                border: none;
                border-radius: 6px;
                padding: 8px 12px;
                text-align: left;
                font-size: 13px;
                margin: 1px 0px;
            }
            
            QPushButton#tabButton:hover {
                background-color: #2C2C2C;
            }
            
            QPushButton#tabButton:checked {
                background-color: #007AFF;
                color: #ffffff;
            }
            
            QPushButton#closeBtn {
                background-color: transparent;
                color: #8E8E93;
                border: none;
                border-radius: 10px;
                font-size: 14px;
                font-weight: bold;
            }
            
            QPushButton#closeBtn:hover {
                background-color: #FF3B30;
                color: #ffffff;
            }
            
            /* Content Area */
            QStackedWidget#contentArea {
                background-color: #0A0A0A;
                border: none;
            }
            
            /* Navigation Controls */
            QPushButton {
                background-color: #2C2C2C;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-size: 14px;
            }
            
            QPushButton:hover {
                background-color: #3C3C3C;
            }
            
            QPushButton:pressed {
                background-color: #4C4C4C;
            }
            
            /* URL Bar */
            QLineEdit {
                background: rgba(255, 255, 255, 0.1);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 13px;
            }
            
            QLineEdit:focus {
                border-color: #007AFF;
                background: rgba(255, 255, 255, 0.15);
            }
            
            /* ComboBox */
            QComboBox {
                background-color: #2C2C2C;
                color: #ffffff;
                border: 1px solid #3C3C3C;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
            }
            
            QComboBox:hover {
                border-color: #007AFF;
                background-color: #3C3C3C;
            }
            
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            
            QComboBox QAbstractItemView {
                background-color: #2C2C2C;
                color: #ffffff;
                selection-background-color: #007AFF;
                border: 1px solid #3C3C3C;
                border-radius: 6px;
            }
            
            /* Scroll Area */
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                border-radius: 4px;
            }
            
            QScrollBar::handle:vertical {
                background: #3C3C3C;
                border-radius: 4px;
                min-height: 20px;
            }
            
            QScrollBar::handle:vertical:hover {
                background: #4C4C4C;
            }
        """)

    def style_navigation_buttons(self):
        # Find all actions in the toolbar and style them
        toolbar = self.findChild(QToolBar)
        if toolbar:
            for action in toolbar.actions():
                # Create modern button styling
                widget = toolbar.widgetForAction(action)
                if widget and hasattr(widget, 'setStyleSheet'):
                    widget.setStyleSheet("""
                        QPushButton {
                            background-color: #3a3a3a;
                            color: #ffffff;
                            border: none;
                            border-radius: 8px;
                            padding: 8px 12px;
                            font-weight: bold;
                        }
                        
                        QPushButton:hover {
                            background-color: #ff6b35;
                        }
                        
                        QPushButton:pressed {
                            background-color: #e55a2e;
                        }
                    """)

    def tab_open_doubleclick(self, i):
        if i == -1:
            self.add_new_tab()

    def filter_console_messages(self, level, message, lineNumber, sourceID):
        """Filter out specific console warnings that are not critical"""
        # Suppress common non-critical warnings
        suppress_messages = [
            "iframe which has both allow-scripts and allow-same-origin",
            "Error with Permissions-Policy header",
            "Unrecognized feature: 'payment'",
            "Unrecognized feature: 'usb'",
            "Unrecognized feature: 'geolocation'",
            "Unrecognized feature: 'camera'",
            "Unrecognized feature: 'microphone'",
        ]
        
        # Check if this message should be suppressed
        if message:
            for suppress_pattern in suppress_messages:
                if suppress_pattern.lower() in message.lower():
                    return  # Don't log this message

    def current_tab_changed(self, i):
        current_browser = self.get_current_browser()
        if current_browser and isinstance(current_browser, QWebEngineView):
            qurl = current_browser.url()
            self.update_urlbar(qurl, current_browser)
            self.update_title(current_browser)

    def close_current_tab(self, i):
        if len(self.tab_buttons) < 2:
            return

        self.close_tab(i)
    
    def close_tab(self, tab_index):
        """Close the specified tab in sidebar system"""
        if len(self.web_views) <= 1:
            return  # Don't close the last tab

        # Remove browser widget from content area
        browser = self.web_views[tab_index]
        
        # Clean up password timer for this page
        if hasattr(self, 'password_timers') and browser.page() in self.password_timers:
            self.password_timers[browser.page()].stop()
            del self.password_timers[browser.page()]
        
        self.content_area.removeWidget(browser)
        browser.deleteLater()

        # Remove from web views list
        self.web_views.pop(tab_index)

        # Remove tab widget from sidebar
        tab_info = self.tab_widgets.pop(tab_index)
        tab_info['widget'].deleteLater()

        # Update indices for remaining tabs and reconnect signals
        for i, tab_info in enumerate(self.tab_widgets):
            tab_info['index'] = i
            tab_info['button'].clicked.disconnect()
            tab_info['button'].clicked.connect(lambda checked, idx=i: self.set_current_tab(idx))
            tab_info['close_btn'].clicked.disconnect()
            tab_info['close_btn'].clicked.connect(lambda checked, idx=i: self.close_tab(idx))

        # Switch to another tab
        if tab_index >= len(self.web_views):
            tab_index = len(self.web_views) - 1
        self.set_current_tab(tab_index)

    def get_favicon_as_text(self, browser):
        """Extract favicon from browser and convert to text representation"""
        if not isinstance(browser, QWebEngineView):
            return "🌐"
            
        # Get the actual favicon from the browser
        icon = browser.icon()
        
        if not icon.isNull():
            # Try to convert favicon to a simple text representation
            # For now, we'll use a generic icon but this could be enhanced
            return "�"  # Use earth icon for sites with favicons
        
        # Fallback emoji icons for common sites (minimal set)
        url_str = browser.url().toString().lower()
        
        # Handle PyBrowse home page
        if 'pybrowse_home.html' in url_str or url_str.startswith('file://'):
            return "🚀"
            
        # Quick favicon mapping for very common sites only
        favicon_map = {
            'google.com': '🔍',
            'youtube.com': '📺', 
            'github.com': '🐙',
            'stackoverflow.com': '📚',
            'wikipedia.org': '📖',
            'reddit.com': '🤖',
            'x.com': '❌',
            'facebook.com': '📘',
            'instagram.com': '📸',
            'linkedin.com': '💼',
        }
        
        for domain, emoji in favicon_map.items():
            if domain in url_str:
                return emoji
                
        return "🌐"  # Default fallback

    def on_page_load_finished(self, success, tab_index, browser):
        """Handle page load completion"""
        if success:
            # Update tab title
            self.update_tab_title(tab_index, browser)
            
            # Check if this is the settings page and populate it
            if isinstance(browser, QWebEngineView):
                url = browser.url().toString()
                if 'settings.html' in url:
                    self.populate_settings_page(browser)
                else:
                    # Set up password management for regular web pages
                    self.setup_password_management(browser)

    def setup_password_management(self, browser):
        """Set up password detection and auto-fill for a browser tab"""
        if not isinstance(browser, QWebEngineView):
            return
            
        page = browser.page()
        if not page:
            return
            
        url = browser.url().toString()
        
        # Skip password management for local files and special URLs
        if url.startswith('file://') or url.startswith('about:') or url.startswith('chrome://'):
            return
        
        # Extract domain
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.replace('www.', '')
            
            if domain:
                # Inject password detection script
                self.password_manager.inject_password_detection_script(page)
                
                # Try to auto-fill if we have saved passwords
                QTimer.singleShot(1000, lambda: self.password_manager.auto_fill_passwords(page, domain))
                
                # Set up periodic checking for password save requests
                def check_password_saves():
                    self.password_manager.check_for_password_save(page)
                
                timer = QTimer()
                timer.timeout.connect(check_password_saves)
                timer.start(2000)  # Check every 2 seconds
                
                # Store timer associated with the page for cleanup
                if not hasattr(self, 'password_timers'):
                    self.password_timers = {}
                self.password_timers[page] = timer
                
                # Clean up timer when page is destroyed
                def cleanup_timer():
                    if page in self.password_timers:
                        self.password_timers[page].stop()
                        del self.password_timers[page]
                
                page.destroyed.connect(cleanup_timer)
                
        except Exception as e:
            print(f"Error setting up password management: {e}")

    def populate_settings_page(self, browser):
        """Inject data into the settings page"""
        if not isinstance(browser, QWebEngineView):
            return
            
        # Get statistics from database
        history_data = self.db.get_history(1000)  # Get more data for stats
        session_data = self.db.restore_session()
        password_data = self.db.get_saved_passwords()
        
        # Calculate statistics
        total_history = len(history_data)
        unique_sites = len(set(url for url, _, _, _ in history_data))
        total_visits = sum(visits for _, _, _, visits in history_data)
        current_tabs = len(self.tab_buttons)
        saved_passwords = len(password_data)
        
        # Prepare JavaScript to update the page
        js_code = f"""
        // Update statistics
        document.getElementById('total-history').textContent = '{total_history}';
        document.getElementById('unique-sites').textContent = '{unique_sites}';
        document.getElementById('total-visits').textContent = '{total_visits}';
        document.getElementById('current-tabs').textContent = '{current_tabs}';
        
        // Update password stats if element exists
        const passwordStats = document.getElementById('saved-passwords');
        if (passwordStats) {{
            passwordStats.textContent = '{saved_passwords}';
        }}
        
        // Populate history
        const historyContainer = document.getElementById('history-container');
        historyContainer.innerHTML = '';
        """
        
        # Add history items (limit to 20 for performance)
        for i, (url, title, visit_time, visit_count) in enumerate(history_data[:20]):
            # Escape quotes for JavaScript
            safe_title = (title or url).replace("'", "\\'").replace('"', '\\"')
            safe_url = url.replace("'", "\\'").replace('"', '\\"')
            safe_time = visit_time.replace("'", "\\'").replace('"', '\\"')
            
            js_code += f"""
            const historyItem{i} = document.createElement('div');
            historyItem{i}.className = 'history-item';
            historyItem{i}.innerHTML = `
                <div class="history-title">{safe_title}</div>
                <div class="history-url">{safe_url}</div>
                <div class="history-meta">
                    <span>Visited: {safe_time}</span>
                    <span>{visit_count} visits</span>
                </div>
            `;
            historyItem{i}.onclick = () => navigateToUrl('{safe_url}');
            historyContainer.appendChild(historyItem{i});
            """
        
        if not history_data:
            js_code += """
            historyContainer.innerHTML = '<div class="no-history">No browsing history found</div>';
            """
        
        # Set up direct JavaScript-Python communication
        js_code += """
        // Override the clearHistory function - no confirmation dialog
        window.clearHistory = function() {
            console.log('clearHistory called - clearing immediately');
            
            // Set a flag that Python can check
            window.shouldClearHistory = true;
            
            // Show loading message
            const historyContainer = document.getElementById('history-container');
            historyContainer.innerHTML = '<div style="text-align: center; padding: 20px; color: #888;">Clearing history...</div>';
            
            // Trigger Python check
            setTimeout(function() {
                location.reload();
            }, 100);
        };
        
        // Override clearAllData function - no confirmation dialog  
        window.clearAllData = function() {
            console.log('clearAllData called - clearing immediately');
            
            // Set a flag that Python can check
            window.shouldClearAllData = true;
            
            // Show loading message
            const historyContainer = document.getElementById('history-container');
            historyContainer.innerHTML = '<div style="text-align: center; padding: 20px; color: #888;">Clearing all data...</div>';
            
            // Trigger Python check
            setTimeout(function() {
                location.reload();
            }, 100);
        };
        
        window.refreshHistory = function() {
            location.reload();
        };
        """
        
        # Execute the JavaScript and set up polling for action flags
        page = browser.page()
        if page:
            page.runJavaScript(js_code)
            
            # Set up a timer to check for JavaScript action flags
            def check_javascript_actions():
                def handle_clear_history(result):
                    if result:
                        print("Clear history flag detected, clearing history...")
                        self.clear_browser_history()
                        # Refresh the page after clearing
                        QTimer.singleShot(200, lambda: page.runJavaScript("location.reload();"))
                
                def handle_clear_all_data(result):
                    if result:
                        print("Clear all data flag detected, clearing all data...")
                        self.clear_all_browser_data()
                        # Refresh the page after clearing
                        QTimer.singleShot(200, lambda: page.runJavaScript("location.reload();"))
                
                # Check for clear history flag
                page.runJavaScript("window.shouldClearHistory || false", handle_clear_history)
                
                # Check for clear all data flag
                page.runJavaScript("window.shouldClearAllData || false", handle_clear_all_data)
            
            # Start polling every 500ms
            timer = QTimer()
            timer.timeout.connect(check_javascript_actions)
            timer.start(500)
            
            # Store timer in main browser object to prevent garbage collection
            if not hasattr(self, 'action_timers'):
                self.action_timers = []
            self.action_timers.append(timer)

    def add_to_history(self, browser):
        """Add current page to browsing history"""
        if isinstance(browser, QWebEngineView):
            url = browser.url().toString()
            page = browser.page()
            title = page.title() if page else ""
            self.db.add_to_history(url, title)

    def get_current_session_data(self):
        """Get data for all currently open tabs"""
        tabs_data = []
        
        for i in range(self.content_area.count()):
            browser = self.content_area.widget(i)
            if isinstance(browser, QWebEngineView):
                url = browser.url().toString()
                page = browser.page()
                title = page.title() if page else ""
                is_current = (i == self.current_tab_index)
                tabs_data.append((url, title, is_current))
        
        return tabs_data

    def save_current_session(self):
        """Save current browser session to database"""
        session_data = self.get_current_session_data()
        self.db.save_session(session_data)

    def restore_previous_session(self):
        """Restore tabs from previous session"""
        session_data = self.db.restore_session()
        
        if not session_data:
            # No previous session, create default tab
            self.add_new_tab()
            return
        
        # Clear existing tabs first
        while self.content_area.count() > 0:
            widget = self.content_area.widget(0)
            self.content_area.removeWidget(widget)
            if widget:
                widget.deleteLater()
        
        # Clear tab buttons
        for button in self.tab_buttons:
            button.setParent(None)
            button.deleteLater()
        self.tab_buttons.clear()
        
        current_tab_index = 0
        # Restore each tab
        for tab_index, url, title, is_current in session_data:
            if url:
                qurl = QUrl(url)
                self.add_new_tab(qurl, title or "Restored Tab")
                if is_current:
                    current_tab_index = tab_index
        
        # Set the previously active tab
        if current_tab_index < len(self.tab_buttons):
            self.set_current_tab(current_tab_index)

    def show_history(self):
        """Show browsing history in a dialog"""
        history_data = self.db.get_history(50)  # Get last 50 entries
        
        dialog = QDialog(self)
        dialog.setWindowTitle("📚 Browsing History")
        dialog.setMinimumSize(800, 600)
        
        layout = QVBoxLayout(dialog)
        
        # Create history list widget
        history_list = QListWidget()
        
        for url, title, visit_time, visit_count in history_data:
            display_title = title if title else url
            item_text = f"{display_title}\n{url}\nVisited: {visit_time} ({visit_count} times)"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, url)  # Store URL for navigation
            history_list.addItem(item)
        
        # Double-click to navigate to URL
        history_list.itemDoubleClicked.connect(self.navigate_to_history_item)
        
        layout.addWidget(history_list)
        
        # Add buttons
        button_layout = QHBoxLayout()
        
        clear_btn = QPushButton("Clear History")
        clear_btn.clicked.connect(lambda: self.clear_history_and_refresh(dialog, history_list))
        button_layout.addWidget(clear_btn)
        
        button_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
        
        dialog.exec()

    def navigate_to_history_item(self, item):
        """Navigate to a URL from history"""
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            current_browser = self.get_current_browser()
            if current_browser and isinstance(current_browser, QWebEngineView):
                current_browser.setUrl(QUrl(url))

    def show_password_manager(self):
        """Show password manager dialog"""
        passwords = self.db.get_saved_passwords()
        
        dialog = QDialog(self)
        dialog.setWindowTitle("🔐 Password Manager")
        dialog.setMinimumSize(800, 600)
        
        layout = QVBoxLayout(dialog)
        
        # Header
        header = QLabel("Saved Passwords")
        header.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)
        
        # Create password list
        password_list = QTableWidget()
        password_list.setColumnCount(4)
        password_list.setHorizontalHeaderLabels(["Website", "Username", "Password", "Last Used"])
        
        # Set column widths
        password_list.setColumnWidth(0, 200)
        password_list.setColumnWidth(1, 150)
        password_list.setColumnWidth(2, 150)
        password_list.setColumnWidth(3, 150)
        
        # Populate table
        password_list.setRowCount(len(passwords))
        for row, (url, domain, username, password, last_used) in enumerate(passwords):
            # Website
            password_list.setItem(row, 0, QTableWidgetItem(domain))
            
            # Username
            password_list.setItem(row, 1, QTableWidgetItem(username))
            
            # Password (masked)
            password_item = QTableWidgetItem("•" * len(password))
            password_item.setData(Qt.ItemDataRole.UserRole, password)  # Store real password
            password_list.setItem(row, 2, password_item)
            
            # Last used
            password_list.setItem(row, 3, QTableWidgetItem(last_used))
            
            # Store full data for deletion
            website_item = password_list.item(row, 0)
            if website_item:
                website_item.setData(Qt.ItemDataRole.UserRole, (url, username))
        
        layout.addWidget(password_list)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        # Show/Hide password button
        toggle_btn = QPushButton("👁️ Show Passwords")
        toggle_btn.setCheckable(True)
        toggle_btn.toggled.connect(lambda checked: self.toggle_password_visibility(password_list, checked))
        button_layout.addWidget(toggle_btn)
        
        # Delete selected button
        delete_btn = QPushButton("🗑️ Delete Selected")
        delete_btn.clicked.connect(lambda: self.delete_selected_password(password_list))
        button_layout.addWidget(delete_btn)
        
        # Clear all button
        clear_all_btn = QPushButton("🧹 Clear All")
        clear_all_btn.clicked.connect(lambda: self.clear_all_passwords_and_refresh(dialog, password_list))
        button_layout.addWidget(clear_all_btn)
        
        button_layout.addStretch()
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
        
        dialog.exec()

    def toggle_password_visibility(self, table, show_passwords):
        """Toggle between showing and hiding passwords"""
        for row in range(table.rowCount()):
            password_item = table.item(row, 2)
            if password_item:
                real_password = password_item.data(Qt.ItemDataRole.UserRole)
                if show_passwords:
                    password_item.setText(real_password)
                else:
                    password_item.setText("•" * len(real_password))

    def delete_selected_password(self, table):
        """Delete the selected password"""
        current_row = table.currentRow()
        if current_row >= 0:
            item = table.item(current_row, 0)
            if item:
                url, username = item.data(Qt.ItemDataRole.UserRole)
                
                reply = QMessageBox.question(self, 'Delete Password',
                                           f'Delete password for {username}?',
                                           QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                
                if reply == QMessageBox.StandardButton.Yes:
                    self.db.delete_password(url, username)
                    table.removeRow(current_row)
                    QMessageBox.information(self, 'Password Deleted', 'Password has been deleted.')

    def clear_all_passwords_and_refresh(self, dialog, table):
        """Clear all passwords and refresh the dialog"""
        reply = QMessageBox.question(self, 'Clear All Passwords',
                                   'Are you sure you want to delete ALL saved passwords?',
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            self.db.clear_all_passwords()
            table.setRowCount(0)
            QMessageBox.information(self, 'Passwords Cleared', 'All passwords have been deleted.')

    def clear_history_and_refresh(self, dialog, history_list):
        """Clear history and refresh the dialog"""
        reply = QMessageBox.question(self, 'Clear History', 
                                   'Are you sure you want to clear all browsing history?',
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            self.db.clear_history()
            history_list.clear()
            QMessageBox.information(self, 'History Cleared', 'Browsing history has been cleared.')

    def clear_browser_history(self):
        """Clear browser history (called from JavaScript)"""
        self.db.clear_history()
        print("Browser history cleared")

    def clear_all_browser_data(self):
        """Clear all browser data including history and sessions (called from JavaScript)"""
        # Clear history
        self.db.clear_history()
        
        # Clear sessions by saving an empty session
        self.db.save_session([])
        
        print("All browser data cleared")

    def get_clean_title(self, raw_title, url):
        """Get actual website title from the loaded page"""
        url_string = url.toString()
        
        # Special handling ONLY for PyBrowse home page specifically
        if 'pybrowse_home.html' in url_string:
            return "PyBrowse Home"
        
        # Use the actual page title if available
        if raw_title and raw_title.strip():
            clean_title = raw_title.strip()
            
            # Limit excessively long titles
            if len(clean_title) > 30:
                clean_title = clean_title[:27] + "..."
                
            return clean_title
        
        # Fallback: extract domain name if no title
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url_string)
            domain = parsed.netloc.replace('www.', '')
            if domain:
                return domain
            return "Loading..."
        except:
            return "New Page"

    def update_tab_title(self, tab_index, browser):
        if isinstance(browser, QWebEngineView) and 0 <= tab_index < len(self.tab_widgets):
            page = browser.page()
            if page:
                raw_title = page.title()
                url = browser.url()
                
                # Get clean title - just the website name
                clean_title = self.get_clean_title(raw_title, url)
                
                # Update the tab button text in sidebar
                self.tab_widgets[tab_index]['button'].setText(clean_title)

    def update_tab_title_on_url_change(self, tab_index, browser, qurl):
        """Update tab title immediately when URL changes for better UX"""
        if isinstance(browser, QWebEngineView) and 0 <= tab_index < len(self.tab_widgets):
            # Show loading state while page loads
            self.tab_widgets[tab_index]['button'].setText("Loading...")

    def update_title(self, browser):
        current_browser = self.web_views[self.current_tab_index] if self.web_views else None
        if browser != current_browser:
            return

        if isinstance(browser, QWebEngineView):
            page = browser.page()
            if page:
                title = page.title()
                if title:
                    self.setWindowTitle(f"{title} - PyBrowse")
                else:
                    self.setWindowTitle("PyBrowse - Privacy-First Browser")

    def navigate_back(self):
        current_browser = self.get_current_browser()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.back()

    def navigate_forward(self):
        current_browser = self.get_current_browser()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.forward()

    def navigate_reload(self):
        current_browser = self.get_current_browser()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.reload()

    def navigate_stop(self):
        current_browser = self.get_current_browser()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.stop()

    def navigate_home(self):
        current_browser = self.get_current_browser()
        if current_browser and isinstance(current_browser, QWebEngineView):
            html_file = resource_path("pybrowse_home.html")
            current_browser.setUrl(QUrl.fromLocalFile(html_file))
    
    def navigate_to_settings(self):
        current_browser = self.get_current_browser()
        if current_browser and isinstance(current_browser, QWebEngineView):
            settings_file = resource_path("settings.html")
            current_browser.setUrl(QUrl.fromLocalFile(settings_file))
    
    def get_current_browser(self):
        """Get the currently active browser widget"""
        if self.web_views and 0 <= self.current_tab_index < len(self.web_views):
            return self.web_views[self.current_tab_index]
        return None

    def get_search_url(self, query, engine="Google"):
        search_engines = {
            "Google": "https://www.google.com/search?q=",
            "Bing": "https://www.bing.com/search?q=",
            "DuckDuckGo": "https://duckduckgo.com/?q=",
            "Brave": "https://search.brave.com/search?q=",
            "Ecosia": "https://www.ecosia.org/search?q="
        }
        return search_engines.get(engine, search_engines["Google"]) + query

    def perform_search(self, query, lucky=False):
        current_engine = self.search_engine_combo.currentText()
        
        if lucky and current_engine == "Google":
            # Google "I'm Feeling Lucky" search
            search_url = f"https://www.google.com/search?q={query}&btnI=1"
        else:
            search_url = self.get_search_url(query, current_engine)
        
        current_browser = self.get_current_browser()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.setUrl(QUrl(search_url))

    def navigate_to_url(self):
        text = self.url_bar.text().strip()
        
        # Regular URL navigation
        q = QUrl(text)
        if q.scheme() == "":
            # If it looks like a search query (no dots or starts with known patterns)
            if "." not in text or text.startswith(("what is", "how to", "why")):
                self.perform_search(text)
                return
            else:
                q.setScheme("http")
        
        current_browser = self.get_current_browser()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.setUrl(q)

    def update_urlbar(self, q, browser=None):
        current_browser = self.get_current_browser()
        if browser != current_browser:
            return

        self.url_bar.setText(q.toString())
        self.url_bar.setCursorPosition(0)

    def closeEvent(self, event):
        """Save current session before closing"""
        self.save_current_session()
        event.accept()


app = QApplication(sys.argv)
QApplication.setApplicationName("PyBrowse")
QApplication.setApplicationDisplayName("PyBrowse - Privacy-First Browser")
window = Browser()
app.exec()