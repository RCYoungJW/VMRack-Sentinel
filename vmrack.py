import os
import sys
import threading
import time
import subprocess
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import ctypes

# ── DPI 感知（确保 4K 屏不模糊） ───────────────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ── Playwright 导入 ────────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False


# ── 路径补丁：确保打包成单文件后仍能找到内核 ────────────────────────────────────
def _get_browser_path():
    if getattr(sys, 'frozen', False):
        # 打包后的 EXE 运行目录
        base = os.path.dirname(sys.executable)
    else:
        # 源代码运行目录
        base = os.path.dirname(os.path.abspath(__file__))
        
    path = os.path.join(base, "playwright_browsers")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = path
    return path


BROWSER_PATH = _get_browser_path()
SESSION_FILE = os.path.join(os.path.dirname(BROWSER_PATH), "vm_login_state.json")
ACTIVITY_URL = "https://www.vmrack.net/zh-CN/activity/2026-spring"

# ── 声音 ────────────────────────────────────────────────────────────────────
def _beep():
    try:
        import winsound
        winsound.Beep(1800, 600)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
#  调色板 & 字体
# ════════════════════════════════════════════════════════════════════════════
PAL = {
    "bg":          "#F2F2F7",
    "sidebar":     "#FFFFFF",
    "card":        "#FFFFFF",
    "accent":      "#007AFF",   # Apple Blue
    "accent_dark": "#0062CC",
    "danger":      "#FF3B30",
    "success":     "#34C759",
    "warn":        "#FF9F0A",
    "text":        "#1C1C1E",
    "subtext":     "#8E8E93",
    "border":      "#E5E5EA",
    "row_alt":     "#F9F9FB",
}

def _sf(size, weight="normal"):
    families = ["SF Pro Text", "Segoe UI", "Arial"]
    return (families[0], size, weight)


# ════════════════════════════════════════════════════════════════════════════
#  主应用
# ════════════════════════════════════════════════════════════════════════════
class VMRackSentinelApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Sentinel — VMRack Monitor v5.1")
        self.root.geometry("1540x1060") # 主界面扩大 30%
        self.root.minsize(1250, 880)
        self.root.configure(bg=PAL["bg"])

        self.running            = False
        self.target_name        = ""
        self.target_iid         = None
        self._alarm_stop        = threading.Event()
        self._dialog_lock       = threading.Lock()
        self._dialog_showing    = False
        self._scan_lock         = threading.Lock()

        self._setup_styles()
        self._build_ui()

        threading.Thread(target=self._auto_setup, daemon=True).start()

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Mac.Treeview", background=PAL["card"], foreground=PAL["text"], fieldbackground=PAL["card"], rowheight=52, font=_sf(12), borderwidth=0)
        s.configure("Mac.Treeview.Heading", background=PAL["bg"], foreground=PAL["subtext"], font=_sf(11, "bold"), padding=(14, 10))
        s.configure("Thin.Vertical.TScrollbar", background=PAL["border"], troughcolor=PAL["bg"], width=6)

    def _build_ui(self):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=0)
        root.rowconfigure(1, weight=8, minsize=520) # 列表占 80%
        root.rowconfigure(2, weight=0)
        root.rowconfigure(3, weight=2, minsize=220) # 日志占 20%

        top = tk.Frame(root, bg=PAL["card"], highlightthickness=1, highlightbackground=PAL["border"])
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        brand = tk.Frame(top, bg=PAL["card"])
        brand.grid(row=0, column=0, padx=(28, 0), pady=18, sticky="w")
        tk.Label(brand, text="⬡", bg=PAL["card"], fg=PAL["accent"], font=_sf(22, "bold")).pack(side="left", padx=(0, 8))
        tk.Label(brand, text="Sentinel", bg=PAL["card"], fg=PAL["text"], font=_sf(17, "bold")).pack(side="left")
        self._status_dot = tk.Label(brand, text="●", bg=PAL["card"], fg=PAL["subtext"], font=_sf(10))
        self._status_dot.pack(side="left", padx=(10, 0), pady=2)
        self._status_lbl = tk.Label(brand, text="就绪", bg=PAL["card"], fg=PAL["subtext"], font=_sf(10))
        self._status_lbl.pack(side="left", padx=(2, 0))

        btns = tk.Frame(top, bg=PAL["card"])
        btns.grid(row=0, column=2, padx=28, pady=14, sticky="e")

        self.btn_login   = self._pill_btn(btns, "登录账号",   self._do_login,   ghost=True)
        self.btn_scan    = self._pill_btn(btns, "全量扫描",   self._scan_async, ghost=True)
        self.btn_monitor = self._pill_btn(btns, "开始监测",   self._toggle,     ghost=False)
        self.btn_login.pack(side="left", padx=(0, 12))
        self.btn_scan.pack(side="left",  padx=(0, 12))
        self.btn_monitor.pack(side="left")
        
        # 初始状态：灰色背景+白色字
        self.btn_monitor.config(state="disabled", bg="#D1D1D6", fg="white", disabledforeground="white")

        list_wrap = tk.Frame(root, bg=PAL["card"])
        list_wrap.grid(row=1, column=0, sticky="nsew", padx=20, pady=(12, 0))
        list_wrap.columnconfigure(0, weight=1)
        list_wrap.rowconfigure(1, weight=1)

        list_hdr = tk.Frame(list_wrap, bg=PAL["card"])
        list_hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        tk.Label(list_hdr, text="套餐列表", bg=PAL["card"], fg=PAL["text"], font=_sf(13, "bold")).pack(side="left")
        self._count_lbl = tk.Label(list_hdr, text="", bg=PAL["card"], fg=PAL["subtext"], font=_sf(11))
        self._count_lbl.pack(side="left", padx=8)

        tv_frame = tk.Frame(list_wrap, bg=PAL["card"])
        tv_frame.grid(row=1, column=0, sticky="nsew")
        tv_frame.columnconfigure(0, weight=1)
        tv_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(tv_frame, columns=("name", "status"), show="headings", style="Mac.Treeview", selectmode="browse")
        self.tree.heading("name", text="  套餐名称"); self.tree.heading("status", text="状态")
        self.tree.column("name", width=1000, anchor="w"); self.tree.column("status", width=220, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        
        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self.tree.yview, style="Thin.Vertical.TScrollbar")
        vsb.grid(row=0, column=1, sticky="ns"); self.tree.configure(yscrollcommand=vsb.set)

        self.tree.tag_configure("stock", foreground=PAL["success"])
        self.tree.tag_configure("sold", foreground=PAL["danger"])
        self.tree.tag_configure("alt", background=PAL["row_alt"])

        log_wrap = tk.Frame(root, bg=PAL["card"])
        log_wrap.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 16))
        log_wrap.columnconfigure(0, weight=1); log_wrap.rowconfigure(0, weight=1)

        self.log_box = scrolledtext.ScrolledText(log_wrap, bg=PAL["card"], font=("Consolas", 11), borderwidth=0, relief="flat", padx=16, pady=10, state="disabled")
        self.log_box.grid(row=0, column=0, sticky="nsew")
        self.log_box.tag_config("info", foreground=PAL["text"]); self.log_box.tag_config("success", foreground=PAL["success"])

    def _pill_btn(self, parent, text, cmd, ghost=False):
        if ghost:
            return tk.Button(parent, text=text, command=cmd, font=_sf(12), fg=PAL["accent"], bg=PAL["card"], relief="flat", bd=0, padx=16, pady=7, cursor="hand2")
        # 按钮内边距调整为 26, 7
        return tk.Button(parent, text=text, command=cmd, font=_sf(12, "bold"), fg="white", bg=PAL["accent"], activeforeground="white", activebackground=PAL["accent_dark"], disabledforeground="white", relief="flat", bd=0, padx=26, pady=7, cursor="hand2")

    def log(self, text: str, level="info"):
        self.root.after(0, self._write_log, text, level)

    def _write_log(self, text: str, level: str):
        ts = time.strftime("%H:%M:%S")
        self.log_box.config(state="normal")
        self.log_box.insert(tk.END, f"[{ts}]  {text}\n", level); self.log_box.see(tk.END)
        self.log_box.config(state="disabled")

    def _set_status(self, text: str, color=None):
        color = color or PAL["subtext"]
        self.root.after(0, lambda: (self._status_dot.config(fg=color), self._status_lbl.config(text=text, fg=color)))

    def _auto_setup(self):
        if not PLAYWRIGHT_OK: self.log("⚠ 缺失 Playwright 库", "info"); return
        try:
            with sync_playwright() as p:
                try:
                    p.chromium.launch().close()
                    self._set_status("就绪", PAL["success"])
                except Exception:
                    self.log("正在安装浏览器环境，请稍候...", "info")
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)
                    self._set_status("就绪", PAL["success"])
        except Exception as e:
            self.log(f"环境自检异常: {e}")

    def _core_scanner(self, is_monitoring=False):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx_kwargs = {"storage_state": SESSION_FILE} if os.path.exists(SESSION_FILE) else {}
                context = browser.new_context(no_viewport=True, **ctx_kwargs)
                page = context.new_page()
                page.goto(ACTIVITY_URL, timeout=50000, wait_until="domcontentloaded")
                results = page.evaluate("""async () => {
                    for (let i = 0; i < 20; i++) { window.scrollTo(0, i * 600); await new Promise(r => setTimeout(r, 120)); }
                    await new Promise(r => setTimeout(r, 1800));
                    const data = []; const seen = new Set();
                    document.querySelectorAll('div, section').forEach(card => {
                        const raw = card.innerText || ''; if (!raw.includes('VPS') || !raw.includes('$')) return;
                        const nameLine = raw.split('\\n').find(l => l.includes('VPS'));
                        if (!nameLine || seen.has(nameLine)) return; seen.add(nameLine);
                        const isSold = raw.includes('售罄') || raw.includes('Sold');
                        data.push({ name: nameLine.trim(), status: isSold ? '❌ 售罄' : '✅ 有货' });
                    });
                    return data;
                }""")
                browser.close(); return results
        except Exception as e:
            self.log(f"扫描引擎异常: {e}"); return []

    def _scan_async(self):
        if not self._scan_lock.acquire(blocking=False): return
        self.btn_scan.config(state="disabled")
        threading.Thread(target=self._scan_task, daemon=True).start()

    def _scan_task(self):
        try:
            results = self._core_scanner()
            self.root.after(0, self._update_tree, results)
        finally:
            self._scan_lock.release()
            self.root.after(0, lambda: self.btn_scan.config(state="normal"))

    def _update_tree(self, items):
        for i in self.tree.get_children(): self.tree.delete(i)
        for idx, item in enumerate(items):
            tag = ("stock" if "有货" in item["status"] else "sold",) + (("alt",) if idx % 2 == 1 else ())
            self.tree.insert("", "end", values=(f"  {item['name']}", item["status"]), tags=tag)
        # 监测保护：如果正在监测，不显示就绪
        if not self.running:
            self._set_status("就绪", PAL["success"])
            self.btn_monitor.config(state="normal", bg=PAL["accent"])
        self._count_lbl.config(text=f"{len(items)} 个套餐")

    def _toggle(self):
        if self.running:
            self.running = False
            self.btn_monitor.config(text="开始监测", bg=PAL["accent"])
            self._set_status("就绪", PAL["success"]); return
        sel = self.tree.selection()
        if not sel: return
        self.target_name = self.tree.item(sel[0])["values"][0].strip()
        self.running = True
        self.btn_monitor.config(text="停止监测", bg=PAL["danger"])
        self._set_status(f"监测中：{self.target_name[:25]}...", PAL["accent"])
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _monitor_loop(self):
        while self.running:
            self.log(f"↻ 轮询中: {self.target_name}"); results = self._core_scanner(True)
            match = next((r for r in results if r["name"] == self.target_name), None)
            if match:
                self.root.after(0, self._update_tree, results)
                if "有货" in match["status"]:
                    self.running = False
                    self.root.after(0, lambda: self.btn_monitor.config(text="开始监测", bg=PAL["accent"]))
                    self.root.after(0, self._show_alert); break
            time.sleep(10)

    def _show_alert(self):
        if self._dialog_showing: return
        self._dialog_showing = True; self._alarm_stop.clear()
        threading.Thread(target=self._alarm_worker, daemon=True).start()
        # 弹窗 2 倍尺寸并优化布局
        win = tk.Toplevel(self.root); win.title("补货提醒"); win.geometry("960x650")
        win.resizable(False, False); win.configure(bg=PAL["card"]); win.attributes("-topmost", True)
        def _dismiss(): self._alarm_stop.set(); self._dialog_showing = False; win.destroy()
        win.protocol("WM_DELETE_WINDOW", _dismiss)
        tk.Label(win, text="🔔", bg=PAL["card"], font=("", 80)).pack(pady=(40, 10))
        tk.Label(win, text="目标套餐已补货！", bg=PAL["card"], font=_sf(28, "bold")).pack()
        tk.Label(win, text=self.target_name, bg=PAL["card"], fg=PAL["subtext"], font=_sf(18), wraplength=800).pack(pady=(15, 30))
        tk.Button(win, text="我知道了，立即下单", command=_dismiss, font=_sf(16, "bold"), fg="white", bg=PAL["accent"], relief="flat", bd=0, padx=80, pady=18, cursor="hand2").pack(pady=(0, 40))

    def _alarm_worker(self):
        import winsound
        while not self._alarm_stop.is_set(): winsound.Beep(1800, 600); time.sleep(0.35)

    def _do_login(self):
        def _task():
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False); page = browser.new_page()
                page.goto("https://www.vmrack.net/zh-CN/login")
                try: page.wait_for_url(lambda u: "/login" not in u, timeout=0)
                except: pass
                context = page.context; context.storage_state(path=SESSION_FILE)
                self.log("✅ 登录成功"); browser.close()
        threading.Thread(target=_task, daemon=True).start()

if __name__ == "__main__":
    root = tk.Tk(); app = VMRackSentinelApp(root); root.mainloop()