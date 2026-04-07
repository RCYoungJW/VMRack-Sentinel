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
        self.package_urls       = {}  # 存储提取到的套餐专属购买链接
        self._alarm_stop        = threading.Event()
        self._dialog_lock       = threading.Lock()
        self._dialog_showing    = False
        self._scan_lock         = threading.Lock()
        self._monitor_thread_active = False 
        self._env_ready         = False 

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
        if not self._env_ready: return []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge", headless=True)
                ctx_kwargs = {"storage_state": SESSION_FILE} if os.path.exists(SESSION_FILE) else {}
                page = browser.new_context(no_viewport=True, **ctx_kwargs).new_page()
                page.goto(ACTIVITY_URL, timeout=50000, wait_until="domcontentloaded")
                if not is_monitoring: self.log("📡 深度探测中，滚动加载全量数据...", "info")
                results = page.evaluate("""async () => {
                    for (let i = 0; i < 20; i++) { window.scrollTo(0, i * 600); await new Promise(r => setTimeout(r, 120)); }
                    await new Promise(r => setTimeout(r, 1800));
                    const data = []; const seen = new Set();
                    document.querySelectorAll('div, section, article').forEach(card => {
                        const raw = card.innerText || ''; if (!raw.includes('VPS') || !raw.includes('$')) return;
                        const nameLine = raw.split('\\n').find(l => l.includes('VPS'));
                        if (!nameLine || seen.has(nameLine)) return; seen.add(nameLine);
                        
                        // 获取购买链接逻辑
                        let link = 'https://www.vmrack.net/zh-CN/activity/2026-spring';
                        const aNode = card.querySelector('a');
                        if (aNode && aNode.href) { link = aNode.href; }
                        
                        data.push({ 
                            name: nameLine.trim(), 
                            status: (raw.includes('售罄') || raw.includes('Sold')) ? '❌ 售罄' : '✅ 有货',
                            url: link
                        });
                    });
                    return data;
                }""")
                browser.close(); return results
        except Exception as e: self.log(f"⚠ 扫描异常: {e}", "warn"); return []

    def _scan_async(self):
        if not self._scan_lock.acquire(blocking=False): return
        self.btn_scan.config(state="disabled"); self._set_status("扫描中...", PAL["accent"])
        self.log("🚀 启动全量扫描...", "info")
        threading.Thread(target=self._scan_task, daemon=True).start()

    def _scan_task(self):
        try:
            results = self._core_scanner(is_monitoring=False)
            self.root.after(0, self._update_tree, results)
        finally:
            self._scan_lock.release(); self.root.after(0, lambda: self.btn_scan.config(state="normal"))

    def _update_tree(self, items):
        for i in self.tree.get_children(): self.tree.delete(i)
        if not items: return
        for idx, item in enumerate(items):
            clean_name = item['name'].strip()
            self.package_urls[clean_name] = item.get("url", ACTIVITY_URL) # 储存购买链接
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
            match = next((r for r in results if r["name"].strip() == self.target_name.strip()), None)
            if match:
                self.log(f"↻ 轮询结果：{self.target_name}  →  {match['status']}", "info")
                self.root.after(0, self._update_tree, results)
                if "有货" in match["status"]:
                    self.running = False; self.root.after(0, self._show_alert); break
            time.sleep(5)
        self.root.after(0, lambda: self.btn_monitor.config(text="开始监测", bg=PAL["accent"], fg="white"))
        self._set_status("监测已结束", PAL["subtext"]); self._monitor_thread_active = False

    def _show_alert(self):
        if self._dialog_showing: return
        self._dialog_showing = True; self._alarm_stop.clear(); threading.Thread(target=self._alarm_worker, daemon=True).start()
        win = tk.Toplevel(self.root); win.title("补货提醒"); win.geometry("960x650"); win.resizable(False, False); win.configure(bg=PAL["card"]); win.attributes("-topmost", True)
        
        # 提取目标套餐的专属购买链接
        target_url = self.package_urls.get(self.target_name.strip(), ACTIVITY_URL)

        def _dismiss():
            self._alarm_stop.set(); self._dialog_showing = False; win.destroy()
            self.log("✅ 告警已确认，正在为您唤起浏览器前往购买页面...", "success")
            self._open_browser_to_buy(target_url)

        win.protocol("WM_DELETE_WINDOW", _dismiss)
        tk.Label(win, text="🔔", bg=PAL["card"], font=("", 80)).pack(pady=(40, 10))
        tk.Label(win, text="目标套餐已补货！", bg=PAL["card"], font=_sf(28, "bold")).pack()
        tk.Label(win, text=self.target_name.strip(), bg=PAL["card"], fg=PAL["subtext"], font=_sf(18), wraplength=800).pack(pady=(15, 30))
        tk.Button(win, text="我知道了，立即下单", command=_dismiss, font=_sf(16, "bold"), fg="white", bg=PAL["accent"], relief="flat", bd=0, padx=80, pady=18).pack(pady=(0, 40))

    def _alarm_worker(self):
        while not self._alarm_stop.is_set(): _beep(); time.sleep(0.35)

    def _open_browser_to_buy(self, url):
        """调用持久化浏览器档案打开购买链接，确保处于已登录状态"""
        def _task():
            try:
                with sync_playwright() as p:
                    profile_path = os.path.join(_RUN_DIR, "vmrack_profile")
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=profile_path,
                        channel="msedge",
                        headless=False,
                        no_viewport=True
                    )
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(url)
                    # 保持浏览器开启，由用户手动关闭
                    page.wait_for_event("close", timeout=0)
                    context.close()
            except Exception as e:
                self.log(f"❌ 唤起购买页面失败: {e}", "error")
        threading.Thread(target=_task, daemon=True).start()

    def _do_login(self):
        self.log("🚀 打开专属原生浏览器，请完成登录。成功后网页会自动关闭...", "info")
        def _task():
            try:
                with sync_playwright() as p:
                    # 使用持久化上下文(Persistent Context)建立专属档案
                    profile_path = os.path.join(_RUN_DIR, "vmrack_profile")
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=profile_path,
                        channel="msedge",
                        headless=False,
                        no_viewport=True
                    )
                    
                    page = context.pages[0] if context.pages else context.new_page()
                    
                    # SSO 登录链接
                    login_url = "https://sso.vmrack.net/sign-in?redirect=https%253A%252F%252Fwww.vmrack.net"
                    page.goto(login_url)
                    
                    try:
                        # 监测重定向，回到主站代表登录成功
                        page.wait_for_url("https://www.vmrack.net/**", timeout=0)
                    except Exception:
                        pass
                    
                    # 保存 Session 文件供后台隐藏扫描使用
                    context.storage_state(path=SESSION_FILE)
                    self.log("✅ 登录状态已成功保存至本地档案！", "success")
                    context.close()
            except Exception as e:
                if "Target closed" not in str(e) and "has been closed" not in str(e):
                    self.log(f"❌ 登录异常: {e}", "error")
        threading.Thread(target=_task, daemon=True).start()

if __name__ == "__main__":
    root = tk.Tk(); app = VMRackSentinelApp(root); root.mainloop()
