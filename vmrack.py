import os
import sys
import threading
import time
import subprocess
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import ctypes

# ── DPI 感知 (保持 4K 清晰) ──────────────────────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception: pass

# ── Playwright 导入 ────────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

# ── 路径与目录锁定逻辑 (确保配置保存在当前文件夹) ──────────────────────────────────────────
if getattr(sys, 'frozen', False):
    _RUN_DIR = os.path.dirname(sys.executable)
else:
    _RUN_DIR = os.path.dirname(os.path.abspath(__file__))

SESSION_FILE = os.path.join(_RUN_DIR, "vm_login_state.json")
PROFILE_DIR = os.path.join(_RUN_DIR, "vmrack_profile")
ACTIVITY_URL = "https://www.vmrack.net/zh-CN/activity/2026-spring"

def _beep():
    try:
        import winsound
        winsound.Beep(1800, 600)
    except Exception: pass

def _sf(size, weight="normal"):
    families = ["SF Pro Text", "SF Pro Display", ".AppleSystemUIFont", "Segoe UI", "Arial"]
    return (families[0], size, weight)

PAL = {
    "bg": "#F2F2F7", "card": "#FFFFFF", "accent": "#007AFF", 
    "accent_dark": "#0062CC", "danger": "#FF3B30", "success": "#34C759", 
    "warn": "#FF9F0A", "text": "#1C1C1E", "subtext": "#8E8E93", "border": "#E5E5EA", "row_alt": "#F9F9FB"
}

# ════════════════════════════════════════════════════════════════════════════
#  主应用
# ════════════════════════════════════════════════════════════════════════════
class VMRackSentinelApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Sentinel — VMRack Monitor")
        self.root.geometry("1540x1060")
        self.root.minsize(1250, 880)
        self.root.configure(bg=PAL["bg"])

        self.running            = False
        self.target_name        = ""
        self.target_iid         = None
        self.package_urls       = {}  
        self._alarm_stop        = threading.Event()
        self._dialog_lock       = threading.Lock()
        self._dialog_showing    = False
        self._scan_lock         = threading.Lock()
        self._monitor_thread_active = False 
        self._env_ready         = False 
        self._browser_open      = False 

        self._setup_styles()
        self._build_ui()

        self.log("🚀 Sentinel 系统初始化完成", "success")
        threading.Thread(target=self._auto_setup, daemon=True).start()

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Mac.Treeview", background=PAL["card"], foreground=PAL["text"], fieldbackground=PAL["card"], rowheight=52, font=_sf(12), borderwidth=0, relief="flat")
        s.configure("Mac.Treeview.Heading", background=PAL["bg"], foreground=PAL["subtext"], font=_sf(11, "bold"), borderwidth=0, relief="flat", padding=(14, 10))
        s.configure("Thin.Vertical.TScrollbar", background=PAL["border"], troughcolor=PAL["bg"], width=6, relief="flat", borderwidth=0)

    def _build_ui(self):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=8, minsize=520)  
        root.rowconfigure(3, weight=2, minsize=220)  

        top = tk.Frame(root, bg=PAL["card"], highlightthickness=1, highlightbackground=PAL["border"])
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        brand = tk.Frame(top, bg=PAL["card"])
        brand.grid(row=0, column=0, padx=(28, 0), pady=18, sticky="w")
        tk.Label(brand, text="⬡", bg=PAL["card"], fg=PAL["accent"], font=_sf(22, "bold")).pack(side="left", padx=(0, 8))
        tk.Label(brand, text="Sentinel", bg=PAL["card"], fg=PAL["text"], font=_sf(17, "bold")).pack(side="left")
        self._status_dot = tk.Label(brand, text="●", bg=PAL["card"], fg=PAL["subtext"], font=_sf(10))
        self._status_dot.pack(side="left", padx=(10, 0), pady=2)
        self._status_lbl = tk.Label(brand, text="检查环境中...", bg=PAL["card"], fg=PAL["subtext"], font=_sf(10))
        self._status_lbl.pack(side="left", padx=(2, 0))

        btns = tk.Frame(top, bg=PAL["card"])
        btns.grid(row=0, column=2, padx=28, pady=14, sticky="e")
        self.btn_login   = self._pill_btn(btns, "登录账号",   self._do_login,   ghost=True)
        self.btn_scan    = self._pill_btn(btns, "全量扫描",   self._scan_async, ghost=True)
        self.btn_monitor = self._pill_btn(btns, "开始监测",   self._toggle,     ghost=False)
        self.btn_login.pack(side="left", padx=(0, 12)); self.btn_scan.pack(side="left", padx=(0, 12)); self.btn_monitor.pack(side="left")
        
        if os.path.exists(SESSION_FILE):
            self._update_login_btn(is_logged_in=True)

        self.btn_scan.config(state="disabled")
        self.btn_monitor.config(state="disabled", bg="#D1D1D6", fg="white", disabledforeground="white")

        list_wrap = tk.Frame(root, bg=PAL["card"], highlightthickness=1, highlightbackground=PAL["border"])
        list_wrap.grid(row=1, column=0, sticky="nsew", padx=20, pady=(12, 0))
        list_wrap.columnconfigure(0, weight=1); list_wrap.rowconfigure(1, weight=1)
        list_hdr = tk.Frame(list_wrap, bg=PAL["card"]); list_hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        tk.Label(list_hdr, text="套餐列表", bg=PAL["card"], fg=PAL["text"], font=_sf(13, "bold")).pack(side="left")
        self._count_lbl = tk.Label(list_hdr, text="", bg=PAL["card"], fg=PAL["subtext"], font=_sf(11)); self._count_lbl.pack(side="left", padx=8)

        tv_frame = tk.Frame(list_wrap, bg=PAL["card"]); tv_frame.grid(row=1, column=0, sticky="nsew")
        tv_frame.columnconfigure(0, weight=1); tv_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tv_frame, columns=("name", "status"), show="headings", style="Mac.Treeview", selectmode="browse")
        self.tree.heading("name", text="  套餐名称"); self.tree.heading("status", text="状态")
        self.tree.column("name", width=1000, anchor="w"); self.tree.column("status", width=220, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self.tree.yview, style="Thin.Vertical.TScrollbar")
        vsb.grid(row=0, column=1, sticky="ns"); self.tree.configure(yscrollcommand=vsb.set)
        self.tree.tag_configure("stock", foreground=PAL["success"]); self.tree.tag_configure("sold", foreground=PAL["danger"]); self.tree.tag_configure("alt", background=PAL["row_alt"])

        sep_frame = tk.Frame(root, bg=PAL["card"]); sep_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 0))
        tk.Frame(sep_frame, bg=PAL["border"], height=1).pack(fill="x")
        tk.Label(sep_frame, text="系统日志", bg=PAL["card"], fg=PAL["text"], font=_sf(13, "bold")).pack(side="left", padx=16, pady=(8, 4))

        log_wrap = tk.Frame(root, bg=PAL["card"], highlightthickness=1, highlightbackground=PAL["border"])
        log_wrap.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 16))
        log_wrap.columnconfigure(0, weight=1); log_wrap.rowconfigure(0, weight=1)
        self.log_box = scrolledtext.ScrolledText(log_wrap, bg=PAL["card"], fg=PAL["text"], font=("Consolas", 11), borderwidth=0, relief="flat", padx=16, pady=10, state="disabled")
        self.log_box.grid(row=0, column=0, sticky="nsew")
        self.log_box.tag_config("info", foreground=PAL["text"]); self.log_box.tag_config("success", foreground=PAL["success"]); self.log_box.tag_config("warn", foreground=PAL["warn"]); self.log_box.tag_config("error", foreground=PAL["danger"])

    def _pill_btn(self, parent, text, cmd, ghost=False):
        if ghost: return tk.Button(parent, text=text, command=cmd, font=_sf(12), fg=PAL["accent"], bg=PAL["card"], relief="flat", bd=0, padx=16, pady=7, cursor="hand2")
        return tk.Button(parent, text=text, command=cmd, font=_sf(12, "bold"), fg="white", bg=PAL["accent"], activeforeground="white", disabledforeground="white", relief="flat", bd=0, padx=26, pady=7, cursor="hand2")

    def _update_login_btn(self, is_logged_in):
        if is_logged_in:
            self.btn_login.config(text="✅ 已登录", fg=PAL["success"])
        else:
            self.btn_login.config(text="登录账号", fg=PAL["accent"])

    def log(self, text: str, level="info"):
        self.root.after(0, self._write_log, text, level)

    def _write_log(self, text: str, level: str):
        ts = time.strftime("%H:%M:%S")
        self.log_box.config(state="normal")
        self.log_box.insert(tk.END, f"[{ts}]  {text}\n", level); self.log_box.see(tk.END); self.log_box.config(state="disabled")
        self.root.update_idletasks()

    def _set_status(self, text: str, color=None):
        color = color or PAL["subtext"]
        self.root.after(0, lambda: (self._status_dot.config(fg=color), self._status_lbl.config(text=text, fg=color)))

    def _auto_setup(self):
        if not PLAYWRIGHT_OK:
            self.log("❌ 未检测到 Playwright，请在终端执行: pip install playwright", "error")
            self._set_status("环境缺失", PAL["danger"])
            return

        self._env_ready = True
        self.log("✅ Playwright 环境就绪，将直接调用原生浏览器架构。", "success")
        self._set_status("就绪", PAL["success"])
        
        self.root.after(0, lambda: (
            self.btn_scan.config(state="normal"),
            self.btn_monitor.config(state="normal", bg=PAL["accent"])
        ))

    def _core_scanner(self, is_monitoring=False):
        if not self._env_ready: return {"expired": False, "items": []}
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge", headless=True)
                ctx_kwargs = {"storage_state": SESSION_FILE} if os.path.exists(SESSION_FILE) else {}
                page = browser.new_context(no_viewport=True, **ctx_kwargs).new_page()
                page.goto(ACTIVITY_URL, timeout=50000, wait_until="domcontentloaded")
                
                if not is_monitoring: self.log("📡 深度探测中，查询所有套餐...", "info")
                
                results = page.evaluate("""async () => {
                    document.querySelectorAll('script, style, noscript, svg, template').forEach(el => el.remove());
                    for (let i = 0; i < 15; i++) { window.scrollTo(0, i * 600); await new Promise(r => setTimeout(r, 100)); }
                    await new Promise(r => setTimeout(r, 1500));
                    const isLoggedOut = !!document.querySelector('a[href*="/login"], a[href*="sign-in"]');
                    const data = [];
                    const seen = new Set();
                    const defaultUrl = 'https://www.vmrack.net/zh-CN/activity/2026-spring';
                    const actionElements = Array.from(document.querySelectorAll('a, button, div, span')).filter(el => {
                        const txt = (el.innerText || '').replace(/\\s+/g, '');
                        return /立即使用|立即购买|立即下单|立即抢购|售罄|缺货|Sold/i.test(txt) && el.children.length <= 2;
                    });
                    for (const btn of actionElements) {
                        const btnTxt = (btn.innerText || '').replace(/\\s+/g, '');
                        const isSoldOut = /售罄|缺货|Sold/i.test(btnTxt);
                        let card = btn.parentElement;
                        let title = null;
                        for (let i = 0; i < 10; i++) {
                            if (!card || card === document.body) break;
                            const cTxt = card.innerText || '';
                            if (cTxt.includes('VPS')) {
                                const lines = cTxt.split('\\n').map(l => l.trim()).filter(l => l);
                                title = lines.find(l => l.includes('VPS') && !l.includes('{') && !l.includes('['));
                                if (title) break;
                            }
                            card = card.parentElement;
                        }
                        if (title && !seen.has(title)) {
                            seen.add(title);
                            let url = defaultUrl;
                            const aNode = btn.closest('a[href]') || btn.querySelector('a[href]');
                            if (aNode) {
                                url = aNode.href;
                            } else if (card) {
                                const allLinks = Array.from(card.querySelectorAll('a[href]'));
                                const buyLink = allLinks.find(a => /cart|pid|buy|order|checkout/i.test(a.href));
                                if (buyLink) url = buyLink.href;
                                else if (allLinks.length > 0) url = allLinks[allLinks.length - 1].href;
                            }
                            if (url.startsWith('/')) url = window.location.origin + url;
                            data.push({ name: title, status: isSoldOut ? '❌ 售罄' : '✅ 有货', url: url });
                        }
                    }
                    return { expired: isLoggedOut, items: data };
                }""")
                browser.close()
                return results
        except Exception as e: 
            self.log(f"⚠ 扫描异常: {e}", "warn")
            return {"expired": False, "items": []}

    def _scan_async(self):
        if not self._scan_lock.acquire(blocking=False): return
        self.btn_scan.config(state="disabled"); self._set_status("扫描中...", PAL["accent"])
        self.log("🚀 启动全量扫描...", "info")
        threading.Thread(target=self._scan_task, daemon=True).start()

    def _scan_task(self):
        try:
            results = self._core_scanner(is_monitoring=False)
            expired = results.get("expired", False)
            items = results.get("items", [])
            self.root.after(0, lambda: self._handle_scan_result(expired, items))
        finally:
            self._scan_lock.release()
            self.root.after(0, lambda: self.btn_scan.config(state="normal"))

    def _handle_scan_result(self, expired, items):
        self._update_login_btn(not expired)
        if expired and self.btn_login.cget("text") != "登录账号":
            self.log("⚠ 发现登录状态已失效，部分抢购可能受限，请重新登录！", "warn")
        for i in self.tree.get_children(): self.tree.delete(i)
        if not items: return
        for idx, item in enumerate(items):
            clean_name = item['name'].strip()
            self.package_urls[clean_name] = item.get("url", ACTIVITY_URL)
            tag = ("stock" if "有货" in item["status"] else "sold",) + (("alt",) if idx % 2 == 1 else ())
            self.tree.insert("", "end", values=(f"  {clean_name}", item["status"]), tags=tag)
        stock_cnt = sum(1 for i in items if "有货" in i["status"])
        self._count_lbl.config(text=f"{len(items)} 个套餐  ·  {stock_cnt} 个有货")
        if not self.running:
            self.log(f"✅ 扫描完成，共 {len(items)} 项，{stock_cnt} 项有货。", "success")
            self._set_status("就绪", PAL["success"]); self.btn_monitor.config(state="normal", bg=PAL["accent"])

    def _toggle(self):
        if self.running:
            self.running = False; self.btn_monitor.config(text="开始监测", bg=PAL["accent"], fg="white")
            self._set_status("就绪", PAL["success"]); self.log("⏹ 监测已手动停止。", "info"); return
        if self._monitor_thread_active: return
        sel = self.tree.selection()
        if not sel: self.log("⚠ 请先在列表中选择一个套餐。", "warn"); return
        self.target_name = self.tree.item(sel[0])["values"][0].strip(); self.target_iid = sel[0]
        self.running = True; self.btn_monitor.config(text="停止监测", bg=PAL["danger"], fg="white")
        self._set_status(f"监测中：{self.target_name[:25]}...", PAL["accent"])
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _monitor_loop(self):
        self._monitor_thread_active = True
        self.log(f"📡 实时监测锁定：{self.target_name}", "info")
        while self.running:
            results = self._core_scanner(is_monitoring=True)
            if not self.running: break
            expired = results.get("expired", False)
            items = results.get("items", [])
            self.root.after(0, lambda e=expired: self._update_login_btn(not e))
            if expired: self.log("⚠ 警告：监测到登录已掉线失效，请尽快重新登录！", "warn")
            match = next((r for r in items if r["name"].strip() == self.target_name.strip()), None)
            if match:
                self.package_urls[self.target_name.strip()] = match.get("url", ACTIVITY_URL)
                self.log(f"↻ 轮询结果：{self.target_name}  →  {match['status']}", "info")
                self.root.after(0, lambda: self._handle_scan_result(expired, items))
                if "有货" in match["status"]:
                    self.running = False; self.root.after(0, self._show_alert); break
            time.sleep(5)
        self.root.after(0, lambda: self.btn_monitor.config(text="开始监测", bg=PAL["accent"], fg="white"))
        self._set_status("监测已结束", PAL["subtext"]); self._monitor_thread_active = False

    def _show_alert(self):
        if self._dialog_showing: return
        self._dialog_showing = True; self._alarm_stop.clear(); threading.Thread(target=self._alarm_worker, daemon=True).start()
        win = tk.Toplevel(self.root); win.title("补货提醒"); win.geometry("960x650"); win.resizable(False, False); win.configure(bg=PAL["card"]); win.attributes("-topmost", True)
        target_url = self.package_urls.get(self.target_name.strip(), ACTIVITY_URL)
        def _dismiss():
            self._alarm_stop.set(); self._dialog_showing = False; win.destroy()
            self.log(f"✅ 正在为您唤起浏览器前往购买页面...", "success")
            self._open_browser_to_buy(self.target_name.strip(), target_url)
        win.protocol("WM_DELETE_WINDOW", _dismiss)
        tk.Label(win, text="🔔", bg=PAL["card"], font=("", 80)).pack(pady=(40, 10))
        tk.Label(win, text="目标套餐已补货！", bg=PAL["card"], font=_sf(28, "bold")).pack()
        tk.Label(win, text=self.target_name.strip(), bg=PAL["card"], fg=PAL["subtext"], font=_sf(18), wraplength=800).pack(pady=(15, 30))
        tk.Button(win, text="我知道了，立即下单", command=_dismiss, font=_sf(16, "bold"), fg="white", bg=PAL["accent"], relief="flat", bd=0, padx=80, pady=18).pack(pady=(0, 40))

    def _alarm_worker(self):
        while not self._alarm_stop.is_set(): _beep(); time.sleep(0.35)

    def _open_browser_to_buy(self, target_name, url):
        if self._browser_open:
            self.log("⚠ 浏览器已经被占用，请先关闭其他弹出的浏览器窗口。", "warn")
            return
        def _task():
            self._browser_open = True
            try:
                with sync_playwright() as p:
                    context = p.chromium.launch_persistent_context(user_data_dir=PROFILE_DIR, channel="msedge", headless=False, no_viewport=True)
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(url) 
                    if "activity" in page.url:
                        self.log(f"⚡ 未找到静态跳转链接，已启动【自动模拟点击】机制...", "info")
                        try:
                            for _ in range(5):
                                page.evaluate("window.scrollBy(0, 500)")
                                time.sleep(0.1)
                            page.evaluate(f"""(tName) => {{
                                const elements = Array.from(document.querySelectorAll('*'));
                                for (const el of elements) {{
                                    if (el.children.length === 0 && (el.innerText || '').includes(tName)) {{
                                        let card = el.parentElement;
                                        for (let i = 0; i < 8; i++) {{
                                            if (card) {{
                                                const btns = Array.from(card.querySelectorAll('a, button, div')).filter(b => 
                                                    /立即|使用|购买|抢购|下单/.test((b.innerText || '').replace(/\\s+/g, ''))
                                                );
                                                if (btns.length > 0) {{ btns[btns.length - 1].click(); return; }}
                                                card = card.parentElement;
                                            }}
                                        }}
                                    }}
                                }}
                            }}""", target_name)
                        except: pass
                    try: page.wait_for_event("close", timeout=0)
                    except: pass
                    finally:
                        try: context.close()
                        except: pass
            except Exception as e:
                self.log(f"❌ 唤起购买页面失败: {e}", "error")
            finally:
                self._browser_open = False
        threading.Thread(target=_task, daemon=True).start()

    def _do_login(self):
        if getattr(self, '_browser_open', False):
            self.log("⚠ 浏览器已经被占用，请先关闭当前浏览器窗口。", "warn")
            return
        self.log("🚀 打开专属原生浏览器，请完成登录。成功后网页会自动关闭...", "info")
        def _task():
            self._browser_open = True
            try:
                with sync_playwright() as p:
                    context = p.chromium.launch_persistent_context(user_data_dir=PROFILE_DIR, channel="msedge", headless=False, no_viewport=True)
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto("https://sso.vmrack.net/sign-in?redirect=https%253A%252F%252Fwww.vmrack.net")
                    try:
                        page.wait_for_url("https://www.vmrack.net/**", timeout=0)
                        context.storage_state(path=SESSION_FILE)
                        self.log("✅ 登录状态已成功保存至本地档案！", "success")
                        self.root.after(0, lambda: self._update_login_btn(True))
                    except Exception:
                        self.log("ℹ️ 浏览器窗口已关闭（未检测到完成登录）。", "info")
                    finally:
                        try: context.close()
                        except: pass
            except Exception as e:
                if "Target closed" not in str(e) and "has been closed" not in str(e):
                    self.log(f"❌ 登录异常: {e}", "error")
            finally:
                self._browser_open = False
        threading.Thread(target=_task, daemon=True).start()

# ── 🌟 唯一修改：极简图标植入 (不加 ID 绑定，不改任何逻辑) ───────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    
    # 仅针对打包环境加载内置 PNG
    if hasattr(sys, '_MEIPASS'):
        try:
            _img_path = os.path.join(sys._MEIPASS, "icon.png")
            if os.path.exists(_img_path):
                _icon = tk.PhotoImage(file=_img_path)
                root.iconphoto(True, _icon)
        except: pass

    app = VMRackSentinelApp(root)
    root.mainloop()
