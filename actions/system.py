"""Система: громкость, яркость, питание, диагностика ПК, время и скриншоты."""
import ctypes
import datetime
import os
import re
import subprocess
import time

import psutil
from persona import line

from .utils import NO_WINDOW, Reply, parse_number, ps_json, run_ps


# ---------------- громкость / яркость ----------------

def _endpoint():
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetSpeakers()
    if hasattr(dev, "EndpointVolume"):
        return dev.EndpointVolume
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def get_volume():
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def set_volume(v):
    ep = _endpoint()
    ep.SetMasterVolumeLevelScalar(max(0, min(100, v)) / 100, None)
    if v > 0:
        ep.SetMute(0, None)


def set_mute(on):
    _endpoint().SetMute(1 if on else 0, None)


def get_brightness():
    import screen_brightness_control as sbc
    return sbc.get_brightness()[0]


def set_brightness(v):
    import screen_brightness_control as sbc
    sbc.set_brightness(max(0, min(100, v)))


# ---------------- диагностика ----------------

def _dir_size(path, limit=200000):
    total, n = 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            n += 1
            if n > limit:
                return total
    return total


def analyze_pc():
    """Возвращает (список проблем, полный текстовый отчёт)."""
    issues, lines = [], []
    add = lines.append
    now = datetime.datetime.now()
    add(f"ДИАГНОСТИКА ПК — {now:%d.%m.%Y %H:%M}")
    add("=" * 60)

    cpu = psutil.cpu_percent(interval=1.5)
    mem = psutil.virtual_memory()
    add(f"Процессор: загрузка {cpu:.0f}% ({psutil.cpu_count()} потоков)")
    add(f"Память: занято {mem.percent:.0f}% ({mem.used / 2**30:.1f} из {mem.total / 2**30:.1f} ГБ)")
    if cpu > 85:
        issues.append(f"процессор загружен на {cpu:.0f} процентов")
    if mem.percent > 85:
        issues.append(f"оперативная память занята на {mem.percent:.0f} процентов")

    up = now - datetime.datetime.fromtimestamp(psutil.boot_time())
    add(f"Время работы без перезагрузки: {up.days} д {up.seconds // 3600} ч")
    if up.days >= 7:
        issues.append(f"компьютер не перезагружался {up.days} дней")

    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat:
        add(f"Батарея: {bat.percent:.0f}% {'(заряжается)' if bat.power_plugged else ''}")

    add("\nДИСКИ:")
    for part in psutil.disk_partitions():
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        free_gb = u.free / 2**30
        add(f"  {part.mountpoint} {part.fstype}: свободно {free_gb:.1f} ГБ из {u.total / 2**30:.0f} ГБ ({100 - u.percent:.0f}%)")
        if u.percent > 90 or free_gb < 10:
            issues.append(f"на диске {part.mountpoint[0]} мало места — {free_gb:.0f} гигабайт")

    for d in ps_json("Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus"):
        add(f"  Накопитель {d.get('FriendlyName')}: здоровье {d.get('HealthStatus')}, состояние {d.get('OperationalStatus')}")
        if str(d.get("HealthStatus")) not in ("Healthy", "0"):
            issues.append(f"накопитель {d.get('FriendlyName')} сообщает о проблемах со здоровьем")

    tmp = os.environ.get("TEMP", "")
    if tmp:
        size = _dir_size(tmp) / 2**30
        add(f"  Временные файлы (%TEMP%): {size:.1f} ГБ")
        if size > 3:
            issues.append(f"временные файлы занимают {size:.0f} гигабайт")

    add("\nУСТРОЙСТВА С ОШИБКАМИ:")
    bad = ps_json("Get-PnpDevice -PresentOnly | Where-Object {$_.Status -eq 'Error'} | Select-Object FriendlyName,Class")
    for d in bad:
        add(f"  {d.get('FriendlyName')} ({d.get('Class')})")
    if not bad:
        add("  нет")
    else:
        issues.append(f"устройств с ошибкой драйвера: {len(bad)}")

    add("\nОШИБКИ В ЖУРНАЛАХ WINDOWS ЗА 24 ЧАСА (по источникам):")
    groups = ps_json("Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;"
                     "StartTime=(Get-Date).AddDays(-1)} | Group-Object ProviderName | "
                     "Sort-Object Count -Descending | Select-Object -First 12 Count,Name")
    total_err = sum(int(g.get("Count", 0)) for g in groups)
    for g in groups:
        add(f"  {g.get('Count'):>4}  {g.get('Name')}")
    if not groups:
        add("  нет")
    if total_err > 30:
        issues.append(f"в журналах {total_err} ошибок за сутки")

    add("\nПОСЛЕДНИЕ ОШИБКИ:")
    for e in ps_json("Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;"
                     "StartTime=(Get-Date).AddDays(-1)} -MaxEvents 8 | Select-Object "
                     "@{n='T';e={$_.TimeCreated.ToString('dd.MM HH:mm')}},ProviderName,Id,"
                     "@{n='M';e={if($_.Message){$_.Message.Substring(0,[Math]::Min(220,$_.Message.Length))}}}"):
        msg = re.sub(r"\s+", " ", str(e.get("M") or ""))
        add(f"  [{e.get('T')}] {e.get('ProviderName')} #{e.get('Id')}: {msg}")

    crashes = run_ps("(Get-WinEvent -FilterHashtable @{LogName='System';Id=41;"
                     "StartTime=(Get-Date).AddDays(-7)} | Measure-Object).Count")
    bsod = run_ps("(Get-WinEvent -FilterHashtable @{LogName='System';Id=1001;"
                  "ProviderName='Microsoft-Windows-WER-SystemErrorReporting';"
                  "StartTime=(Get-Date).AddDays(-7)} | Measure-Object).Count")
    crashes = int(crashes) if crashes.isdigit() else 0
    bsod = int(bsod) if bsod.isdigit() else 0
    add(f"\nАварийные выключения за 7 дней: {crashes}; синие экраны (BSOD): {bsod}")
    if crashes:
        issues.append(f"аварийных выключений за неделю: {crashes}")
    if bsod:
        issues.append(f"синих экранов за неделю: {bsod}")

    add("\nТОП ПРОЦЕССОВ ПО ПАМЯТИ:")
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"]))
        except Exception:
            pass
    for rss, name in sorted(procs, reverse=True)[:8]:
        add(f"  {rss / 2**20:>7.0f} МБ  {name}")

    add("\nПРОГРАММЫ В АВТОЗАГРУЗКЕ:")
    for s in ps_json("Get-CimInstance Win32_StartupCommand | Select-Object Name,Location"):
        add(f"  {s.get('Name')}  [{s.get('Location')}]")

    add("\n" + "=" * 60)
    add("НАЙДЕННЫЕ ПРОБЛЕМЫ:" if issues else "Серьёзных проблем не найдено.")
    for i in issues:
        add(f"  • {i}")
    return issues, "\n".join(lines)


class SystemMixin:
    """Команды управления компьютером и медиа."""

    def volume(self, c, m):
        if re.search(r"(выключи|выключить|отключи|убери|убрать)\w* звук|без звука|замуть", c):
            set_mute(True)
            return Reply(line("mute"))
        if re.search(r"(включи|включить|верни)\w* звук", c) and parse_number(c) is None:
            set_mute(False)
            return Reply(line("unmute"))
        n = parse_number(c)
        cur = get_volume()
        if re.search(r"громче|прибав|увелич|повыс|выше", c):
            new = cur + (n or 10)
        elif re.search(r"тише|убав|уменьш|пониз|ниже", c):
            new = cur - (n or 10)
        elif n is not None:
            new = n
        else:
            return Reply(line("vol_now", n=cur))
        new = max(0, min(100, new))
        set_volume(new)
        return Reply(line("vol_set", n=new))

    def brightness(self, c, m):
        try:
            cur = get_brightness()
        except Exception:
            return Reply(line("bright_fail"))
        n = parse_number(c)
        if re.search(r"ярче|прибав|увелич|повыс|выше", c):
            new = cur + (n or 15)
        elif re.search(r"темнее|убав|уменьш|пониз|ниже", c):
            new = cur - (n or 15)
        elif n is not None:
            new = n
        else:
            return Reply(line("bright_now", n=cur))
        new = max(0, min(100, new))
        try:
            set_brightness(new)
        except Exception:
            return Reply(line("bright_reject"))
        return Reply(line("bright_set", n=new))

    def power(self, c, m):
        restart = m.group(1).startswith("перезаг") or m.group(1).startswith("перезап")
        flag = "/r" if restart else "/s"

        def do():
            subprocess.Popen(["shutdown", flag, "/t", "10"], creationflags=NO_WINDOW)
            return Reply(line("power_do", what_noun="перезагрузка" if restart else "выключение"))
        self.pending = do
        verb = "перезагрузить" if restart else "выключить"
        return Reply(line("power_ask", what=verb, what_cap=verb.capitalize()))

    def lock(self, c, m):
        ctypes.windll.user32.LockWorkStation()
        return Reply(line("lock"))

    def analyze(self, c, m):
        issues, report = analyze_pc()
        folder = os.path.join(self.base_dir, "reports")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"report_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        os.startfile(path)
        use_ai = self.cfg.get("enable_ai") and self.ai and self.ai.available
        spoken = self.ai.summarize_report(report) if use_ai else None
        if not spoken and self.local:
            spoken = self.local.summarize_report(report)
        if spoken:
            return Reply(spoken)
        if not issues:
            return Reply(line("scan_ok"))
        head = "; ".join(issues[:4])
        return Reply(line("scan_bad", n=len(issues), head=head))

    def top_processes(self, c, m):
        """«Что грузит компьютер» — три процесса, которые больше всего едят процессор и память."""
        procs = list(psutil.process_iter(["name", "memory_info"]))
        for p in procs:
            try:
                p.cpu_percent(None)
            except Exception:
                pass
        time.sleep(1.0)
        cpu, mem = {}, {}
        for p in procs:
            try:
                n = (p.info["name"] or "").removesuffix(".exe")
                if n.lower() in ("system idle process", "idle", ""):
                    continue
                cpu[n] = cpu.get(n, 0) + p.cpu_percent(None) / psutil.cpu_count()
                mem[n] = mem.get(n, 0) + p.info["memory_info"].rss / 2**20
            except Exception:
                pass
        top_cpu = sorted(cpu.items(), key=lambda kv: -kv[1])[:3]
        top_mem = sorted(mem.items(), key=lambda kv: -kv[1])[:3]
        c_txt = ", ".join(f"{n} {v:.0f} процентов" for n, v in top_cpu if v >= 1) or "ничего особо не грузит"
        m_txt = ", ".join(f"{n} {v / 1024:.1f} гигабайта" if v >= 1024 else f"{n} {v:.0f} мегабайт"
                          for n, v in top_mem)
        return Reply(line("top_procs", cpu=c_txt, mem=m_txt))

    def screenshot(self, c, m):
        from PIL import ImageGrab
        folder = os.path.join(os.path.expanduser("~"), "Pictures", "Capri")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"screen_{datetime.datetime.now():%Y%m%d_%H%M%S}.png")
        ImageGrab.grab(all_screens=True).save(path)
        return Reply(line("screenshot"))

