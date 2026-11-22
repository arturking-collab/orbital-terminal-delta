#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ORBIT TERMINAL: MODULE DELTA
A terminal-based orbital flight / docking simulator.
Python 3.9+; standard library only. Responsive layout: full view on wide terminals, compact view on small terminals.
"""
from __future__ import annotations

import math
import os
import random
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

# ---------- ANSI palette ----------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
FG = {
    "white": "\033[97m", "muted": "\033[90m", "cyan": "\033[96m",
    "blue": "\033[94m", "teal": "\033[38;5;43m", "green": "\033[92m",
    "lime": "\033[38;5;154m", "yellow": "\033[93m", "orange": "\033[38;5;208m",
    "red": "\033[91m", "magenta": "\033[95m", "violet": "\033[38;5;141m",
    "darkcyan": "\033[38;5;30m", "darkblue": "\033[38;5;24m", "gray": "\033[38;5;245m",
}


def paint(text: str, color: str = "white", bold: bool = False, dim: bool = False) -> str:
    code = FG.get(color, FG["white"])
    return (BOLD if bold else "") + (DIM if dim else "") + code + str(text) + RESET


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def norm_angle(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def bar(value: float, maximum: float, length: int = 13, color: str = "green") -> str:
    ratio = clamp(value / maximum if maximum else 0, 0, 1)
    filled = int(round(ratio * length))
    chosen = color
    if ratio < 0.25:
        chosen = "red"
    elif ratio < 0.50 and color in ("green", "cyan", "blue"):
        chosen = "yellow"
    return paint("█" * filled, chosen) + paint("░" * (length - filled), "muted")


# ---------- Terminal input ----------
class Terminal:
    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.old = None
        self.windows = (os.name == "nt")

    def __enter__(self):
        if self.windows:
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.GetStdHandle(-11)
                mode = ctypes.c_uint()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            except Exception:
                pass
        else:
            import termios
            import tty
            self.old = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
        # Use the terminal's alternate screen buffer: the simulation is a
        # full-screen app, not a stream of log lines. This prevents the menu
        # from being appended to scrollback on terminals that mishandle ED.
        sys.stdout.write("\033[?1049h\033[?25l\033[2J\033[H")
        sys.stdout.flush()
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.windows and self.old is not None:
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
        # Restore the user's normal terminal and scrollback buffer.
        sys.stdout.write(RESET + "\033[0m\033[?25h\033[?1049l\r\n")
        sys.stdout.flush()

    def read_keys(self) -> List[str]:
        result: List[str] = []
        if self.windows:
            import msvcrt
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    nxt = msvcrt.getwch()
                    result.append({"H": "up", "P": "down", "K": "left", "M": "right"}.get(nxt, ""))
                else:
                    result.extend(self._decode(ch))
            return [x for x in result if x]
        import select
        chunks = []
        while select.select([sys.stdin], [], [], 0)[0]:
            try:
                chunks.append(os.read(self.fd, 64).decode("utf-8", "ignore"))
            except OSError:
                break
        raw = "".join(chunks)
        i = 0
        while i < len(raw):
            if raw.startswith("\x1b[", i) and i + 2 < len(raw):
                seq = raw[i:i + 3]
                key = {"\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left"}.get(seq)
                if key:
                    result.append(key)
                    i += 3
                    continue
            if raw[i] == "\x1b":
                result.append("esc")
            else:
                result.extend(self._decode(raw[i]))
            i += 1
        return [x for x in result if x]

    @staticmethod
    def _decode(ch: str) -> List[str]:
        if ch in ("\r", "\n"):
            return ["enter"]
        if ch in ("\x7f", "\b"):
            return ["backspace"]
        if ch == " ":
            return ["space"]
        return [ch.lower()]


# ---------- Simulation objects ----------
@dataclass
class Drone:
    x: float
    y: float
    vx: float
    vy: float
    hp: int = 3
    cooldown: float = 1.0
    mode: str = "PATROL"
    orbit_phase: float = 0.0
    flash: float = 0.0


@dataclass
class Bolt:
    x: float
    y: float
    vx: float
    vy: float
    life: float = 2.0
    hostile: bool = False


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    glyph: str
    color: str


@dataclass
class LogMessage:
    text: str
    color: str = "muted"
    ttl: float = 8.0


@dataclass
class Ship:
    x: float = 16.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 2.02
    angle: float = math.pi / 2
    fuel: float = 100.0
    energy: float = 100.0
    heat: float = 8.0
    hull: float = 100.0
    shield: float = 55.0
    shield_on: bool = False
    beacon_on: bool = False
    radiator_on: bool = False
    invuln: float = 0.0
    fire_cd: float = 0.0


class Game:
    MU = 70.0
    PLANET_R = 5.6
    DOCK_R = 14.4

    def __init__(self):
        self.ship = Ship()
        self.sim_time = 0.0
        self.sector = 1
        self.score = 0
        self.drones: List[Drone] = []
        self.bolts: List[Bolt] = []
        self.particles: List[Particle] = []
        self.logs: List[LogMessage] = []
        self.spawn_clock = 4.0
        self.drone_cap = 2
        self.paused = False
        self.game_over = False
        self.victory = False
        self.docked_flash = 0.0
        self.last_dock_message = 0.0
        self.dock_attempt_cd = 0.0
        self.pulse_count = 0
        self.kills = 0
        self.mission_started = time.time()
        self.last_damage = 0.0
        self.add_log("MODULE DELTA: системы онлайн. Ожидаю команду пилота.", "cyan")
        self.add_log("Цель: сблизиться со станцией и выполнить мягкую стыковку.", "yellow")
        self.spawn_initial_drones()

    @property
    def station_angle(self) -> float:
        # Dock station moves on a nearby circular orbit.
        station_omega = math.sqrt(self.MU / (self.DOCK_R ** 3))
        return norm_angle(-1.05 + station_omega * self.sim_time)

    def station_state(self) -> Tuple[float, float, float, float, float]:
        a = self.station_angle
        x, y = self.DOCK_R * math.cos(a), self.DOCK_R * math.sin(a)
        speed = math.sqrt(self.MU / self.DOCK_R)
        vx, vy = -speed * math.sin(a), speed * math.cos(a)
        port_heading = norm_angle(a + math.pi)
        return x, y, vx, vy, port_heading

    def add_log(self, msg: str, color: str = "muted"):
        self.logs.insert(0, LogMessage(msg, color, 9.0))
        self.logs = self.logs[:5]

    def spawn_initial_drones(self):
        for i in range(2):
            a = random.uniform(-math.pi, math.pi)
            r = random.uniform(11.5, 18.0)
            speed = math.sqrt(self.MU / r)
            self.drones.append(Drone(r * math.cos(a), r * math.sin(a), -speed * math.sin(a), speed * math.cos(a), hp=2 + self.sector // 3, orbit_phase=a))

    def spawn_drone(self):
        a = random.uniform(-math.pi, math.pi)
        r = random.uniform(14.0, 18.5)
        sp = math.sqrt(self.MU / r)
        self.drones.append(Drone(r * math.cos(a), r * math.sin(a), -sp * math.sin(a), sp * math.cos(a), hp=2 + min(3, self.sector // 3), cooldown=random.uniform(1.4, 2.8), orbit_phase=a))
        self.add_log("СИГНАЛ: обнаружен патрульный дрон.", "orange")

    def particles_at(self, x: float, y: float, color: str = "orange", count: int = 8):
        glyphs = ["·", ".", "✦", "+"]
        for _ in range(count):
            a = random.random() * math.tau
            s = random.uniform(0.25, 1.2)
            self.particles.append(Particle(x, y, math.cos(a) * s, math.sin(a) * s, random.uniform(0.3, 0.9), random.choice(glyphs), color))

    def rotate(self, direction: float):
        self.ship.angle = norm_angle(self.ship.angle + direction * math.radians(11))

    def thrust(self, direction: float = 1.0):
        s = self.ship
        if s.fuel <= 0.05:
            self.add_log("ТОПЛИВО ИСЧЕРПАНО: импульс невозможен.", "red")
            return
        efficiency = 0.48 if s.heat >= 88 else (0.75 if s.heat >= 70 else 1.0)
        magnitude = 0.27 * efficiency * direction
        s.vx += math.cos(s.angle) * magnitude
        s.vy += math.sin(s.angle) * magnitude
        s.fuel = max(0.0, s.fuel - (0.72 if direction > 0 else 0.52))
        s.heat = min(115.0, s.heat + (5.6 if direction > 0 else 3.5))
        self.particles_at(s.x - math.cos(s.angle) * 0.65, s.y - math.sin(s.angle) * 0.65, "cyan", 3)
        if s.heat >= 88 and random.random() < 0.22:
            self.add_log("ПРЕДУПРЕЖДЕНИЕ: перегрев снижает тягу.", "orange")

    def lateral(self, direction: float):
        s = self.ship
        if s.fuel <= 0.05:
            self.add_log("ТОПЛИВО ИСЧЕРПАНО.", "red")
            return
        side = s.angle + math.pi / 2 * direction
        efficiency = 0.55 if s.heat >= 88 else 1.0
        s.vx += math.cos(side) * 0.19 * efficiency
        s.vy += math.sin(side) * 0.19 * efficiency
        s.fuel = max(0.0, s.fuel - 0.58)
        s.heat = min(115.0, s.heat + 4.4)
        self.particles_at(s.x - math.cos(side), s.y - math.sin(side), "blue", 2)

    def fire(self):
        s = self.ship
        if s.fire_cd > 0 or s.energy < 3.0:
            return
        if s.heat >= 96:
            self.add_log("ИМПУЛЬСНАЯ ПУШКА ОТКЛЮЧЕНА: перегрев.", "red")
            s.fire_cd = 0.35
            return
        speed = 7.8
        self.bolts.append(Bolt(s.x + math.cos(s.angle) * 0.8, s.y + math.sin(s.angle) * 0.8,
                               s.vx + math.cos(s.angle) * speed, s.vy + math.sin(s.angle) * speed, 1.75, False))
        s.energy = max(0.0, s.energy - 3.0)
        s.heat = min(115.0, s.heat + 1.8)
        s.fire_cd = 0.22
        self.pulse_count += 1

    def toggle_shield(self):
        s = self.ship
        if not s.shield_on and s.shield < 5:
            self.add_log("ЭНЕРГОЩИТ: недостаточно заряда.", "orange")
            return
        s.shield_on = not s.shield_on
        self.add_log("ЭНЕРГОЩИТ " + ("АКТИВИРОВАН" if s.shield_on else "ОТКЛЮЧЁН"), "cyan" if s.shield_on else "muted")

    def toggle_beacon(self):
        self.ship.beacon_on = not self.ship.beacon_on
        self.add_log("ТРАНСПОНДЕР: " + ("маяк включён — сигнал заметнее" if self.ship.beacon_on else "тихий режим"), "yellow")

    def toggle_radiator(self):
        self.ship.radiator_on = not self.ship.radiator_on
        self.add_log("РАДИАТОР: " + ("раскрыт — усиленное охлаждение" if self.ship.radiator_on else "убран"), "cyan")

    def attempt_dock(self):
        if self.dock_attempt_cd > 0:
            return
        self.dock_attempt_cd = 0.55
        s = self.ship
        dx, dy, dvx, dvy, heading = self.station_state()
        dist = distance(s.x, s.y, dx, dy)
        rel_speed = math.hypot(s.vx - dvx, s.vy - dvy)
        angle_error = abs(norm_angle(s.angle - heading))
        if dist > 2.2:
            self.add_log(f"СТЫКОВКА ОТКЛОНЕНА: дистанция {dist:.1f}, нужно ≤ 2.2.", "orange")
        elif rel_speed > 0.68:
            self.add_log(f"СТЫКОВКА ОТКЛОНЕНА: скорость сближения {rel_speed:.2f}, нужно ≤ 0.68.", "orange")
        elif angle_error > math.radians(22):
            self.add_log(f"СТЫКОВКА ОТКЛОНЕНА: ориентация отличается на {math.degrees(angle_error):.0f}°.", "orange")
        else:
            self.complete_dock()

    def complete_dock(self):
        s = self.ship
        self.sector += 1
        self.score += 1200 + max(0, int(s.hull * 4))
        self.docked_flash = 3.5
        self.add_log(f"СТЫКОВКА УСПЕШНА! СЕКТОР {self.sector:02d}: обслуживание модуля.", "green")
        self.add_log("Заправка, зарядка и ремонт выполнены.", "cyan")
        # The station transfers the module to a fresh orbit insertion point.
        a = self.station_angle
        start_a = norm_angle(a + 0.22)
        r = 16.0
        speed = math.sqrt(self.MU / r)
        s.x, s.y = r * math.cos(start_a), r * math.sin(start_a)
        s.vx, s.vy = -speed * math.sin(start_a), speed * math.cos(start_a)
        s.angle = norm_angle(start_a + math.pi / 2)
        s.fuel = min(100.0, s.fuel + 47.0)
        s.energy = min(100.0, s.energy + 55.0)
        s.hull = min(100.0, s.hull + 35.0)
        s.shield = min(100.0, s.shield + 60.0)
        s.heat = 12.0
        s.shield_on = False
        s.radiator_on = False
        s.beacon_on = False
        self.drones.clear()
        self.bolts.clear()
        self.drone_cap = min(5, 2 + self.sector // 2)
        self.spawn_clock = 7.0
        self.spawn_initial_drones()
        if self.sector > 8:
            self.victory = True

    def damage(self, amount: float, source: str):
        s = self.ship
        if s.invuln > 0:
            return
        if s.shield_on and s.shield > 0:
            absorbed = min(s.shield, amount * 1.8)
            s.shield -= absorbed
            amount = max(0.0, amount - absorbed / 1.8)
            self.particles_at(s.x, s.y, "cyan", 5)
        if amount > 0:
            s.hull -= amount
            self.particles_at(s.x, s.y, "red", 7)
            self.add_log(f"ПОВРЕЖДЕНИЕ: {source} (-{amount:.0f}% корпуса).", "red")
            self.last_damage = self.sim_time
        s.invuln = 0.65
        if s.hull <= 0:
            s.hull = 0
            self.game_over = True
            self.add_log("КРИТИЧЕСКОЕ СОСТОЯНИЕ: модуль потерян.", "red")

    def update(self, dt: float):
        if self.paused or self.game_over or self.victory:
            return
        self.sim_time += dt
        s = self.ship
        self.dock_attempt_cd = max(0.0, self.dock_attempt_cd - dt)
        self.docked_flash = max(0.0, self.docked_flash - dt)
        s.invuln = max(0.0, s.invuln - dt)
        s.fire_cd = max(0.0, s.fire_cd - dt)

        # Gravity (semi-implicit Euler) keeps the ship in a natural inertial trajectory.
        r2 = max(1.0, s.x * s.x + s.y * s.y)
        r = math.sqrt(r2)
        grav = self.MU / (r2 * r)
        s.vx += -s.x * grav * dt
        s.vy += -s.y * grav * dt
        s.x += s.vx * dt
        s.y += s.vy * dt

        speed = math.hypot(s.vx, s.vy)
        # Resource systems
        s.heat = max(0.0, s.heat - (2.3 if s.radiator_on else 0.85) * dt)
        s.energy = min(100.0, s.energy + 1.15 * dt)
        s.shield = min(100.0, s.shield + (0.28 if not s.shield_on else -0.9) * dt)
        if s.shield_on:
            s.energy -= 2.7 * dt
            if s.energy <= 0 or s.shield <= 0:
                s.energy = max(0.0, s.energy)
                s.shield_on = False
                self.add_log("ЭНЕРГОЩИТ отключён: заряд исчерпан.", "orange")
        if s.radiator_on:
            s.energy -= 0.65 * dt
            s.heat = max(0.0, s.heat - 2.8 * dt)
            if s.energy <= 0:
                s.energy = 0
                s.radiator_on = False
                self.add_log("Радиатор убран: нет энергии.", "orange")
        if s.beacon_on:
            s.energy -= 0.48 * dt
            if s.energy <= 0:
                s.energy = 0
                s.beacon_on = False
                self.add_log("Транспондер отключён: нет энергии.", "orange")
        if s.heat >= 100:
            s.hull -= 0.85 * dt
            if random.random() < 0.018:
                self.add_log("КРИТИЧЕСКИЙ ПЕРЕГРЕВ: повреждение систем.", "red")
        if s.hull <= 0:
            s.hull = 0
            self.game_over = True

        # Gravity well hazards and escape boundary.
        if r < self.PLANET_R + 0.45:
            self.damage(24.0, "контакт с планетой")
            if not self.game_over:
                nx, ny = s.x / max(r, 0.1), s.y / max(r, 0.1)
                s.x, s.y = nx * (self.PLANET_R + 0.9), ny * (self.PLANET_R + 0.9)
                s.vx += nx * 1.0
                s.vy += ny * 1.0
        if r > 22.5:
            self.damage(8.0, "выход за безопасную орбиту")
            # soft return boundary
            s.vx -= s.x / r * 0.8 * dt
            s.vy -= s.y / r * 0.8 * dt

        # Passive thermal signature; active beacon and radiator increase detectability.
        signature = s.heat * 0.42 + (42 if s.beacon_on else 0) + (18 if s.radiator_on else 0)
        self.spawn_clock -= dt
        if self.spawn_clock <= 0 and len(self.drones) < self.drone_cap:
            self.spawn_drone()
            self.spawn_clock = random.uniform(11.0, 17.0) / min(1.35, 1.0 + self.sector * 0.025)

        # Drone AI: quiet drift / orbital patrol until the module is detectable.
        for d in list(self.drones):
            d.flash = max(0.0, d.flash - dt)
            d.cooldown -= dt
            dx, dy = s.x - d.x, s.y - d.y
            dist = math.hypot(dx, dy)
            detected = signature > 27 or dist < 6.5
            if detected:
                d.mode = "TRACK"
                desired = math.atan2(dy, dx)
                accel = 0.62 if signature > 50 else 0.42
                d.vx += math.cos(desired) * accel * dt
                d.vy += math.sin(desired) * accel * dt
                dv = math.hypot(d.vx, d.vy)
                maxv = 2.4 + min(0.5, self.sector * 0.04)
                if dv > maxv:
                    d.vx *= maxv / dv
                    d.vy *= maxv / dv
            else:
                d.mode = "PATROL"
                # Light orbital correction around the planet.
                rr2 = max(1, d.x * d.x + d.y * d.y)
                rr = math.sqrt(rr2)
                gg = self.MU * 0.83 / (rr2 * rr)
                d.vx += -d.x * gg * dt
                d.vy += -d.y * gg * dt
            d.x += d.vx * dt
            d.y += d.vy * dt
            # Soft collision / proximity attack.
            if dist < 1.0:
                self.damage(8.0, "перехват дрона")
                d.x += (d.x - s.x) * 0.6
                d.y += (d.y - s.y) * 0.6
            if d.mode == "TRACK" and dist < 10.0 and d.cooldown <= 0 and len(self.bolts) < 40:
                ang = math.atan2(dy, dx)
                self.bolts.append(Bolt(d.x, d.y, math.cos(ang) * 3.9, math.sin(ang) * 3.9, 3.2, True))
                d.cooldown = random.uniform(2.1, 3.0)
            if math.hypot(d.x, d.y) > 25 or math.hypot(d.x, d.y) < self.PLANET_R:
                self.drones.remove(d)

        # Pulse and ion projectiles
        for b in list(self.bolts):
            b.x += b.vx * dt
            b.y += b.vy * dt
            b.life -= dt
            if b.hostile and distance(b.x, b.y, s.x, s.y) < 0.75:
                self.damage(9.0, "ионный импульс")
                if b in self.bolts:
                    self.bolts.remove(b)
                continue
            if not b.hostile:
                for d in list(self.drones):
                    if distance(b.x, b.y, d.x, d.y) < 0.85:
                        d.hp -= 1
                        d.flash = 0.18
                        self.particles_at(d.x, d.y, "magenta", 4)
                        if d.hp <= 0:
                            self.score += 180
                            self.kills += 1
                            self.particles_at(d.x, d.y, "orange", 9)
                            self.drones.remove(d)
                            self.add_log("Дрон выведен из строя. +180 очков.", "green")
                        if b in self.bolts:
                            self.bolts.remove(b)
                        break
            if b.life <= 0 and b in self.bolts:
                self.bolts.remove(b)

        for p in list(self.particles):
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.vx *= 0.97
            p.vy *= 0.97
            p.life -= dt
            if p.life <= 0:
                self.particles.remove(p)

        for item in self.logs:
            item.ttl -= dt
        self.logs = [item for item in self.logs if item.ttl > 0]

    def nav_data(self):
        dx, dy, dvx, dvy, heading = self.station_state()
        s = self.ship
        return (distance(s.x, s.y, dx, dy), math.hypot(s.vx - dvx, s.vy - dvy), abs(norm_angle(s.angle - heading)), dx, dy, heading)


# ---------- Screen drawing ----------
class Canvas:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.chars = [[" " for _ in range(w)] for _ in range(h)]
        self.colors = [["muted" for _ in range(w)] for _ in range(h)]
        self.bold = [[False for _ in range(w)] for _ in range(h)]

    def put(self, x: int, y: int, ch: str, color: str = "white", bold: bool = False):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.chars[y][x] = ch[:1] if ch else " "
            self.colors[y][x] = color
            self.bold[y][x] = bold

    def text(self, x: int, y: int, text: str, color: str = "white", bold: bool = False, maxw: Optional[int] = None):
        limit = self.w if maxw is None else min(self.w, x + maxw)
        xx = x
        for ch in str(text):
            if xx >= limit:
                break
            self.put(xx, y, ch, color, bold)
            xx += 1

    def hline(self, x: int, y: int, n: int, color: str = "darkcyan", ch: str = "─"):
        for xx in range(x, x + n):
            self.put(xx, y, ch, color)

    def vline(self, x: int, y: int, n: int, color: str = "darkcyan", ch: str = "│"):
        for yy in range(y, y + n):
            self.put(x, yy, ch, color)

    def box(self, x: int, y: int, w: int, h: int, color: str = "darkcyan"):
        if w < 2 or h < 2:
            return
        self.put(x, y, "┌", color)
        self.put(x + w - 1, y, "┐", color)
        self.put(x, y + h - 1, "└", color)
        self.put(x + w - 1, y + h - 1, "┘", color)
        self.hline(x + 1, y, w - 2, color)
        self.hline(x + 1, y + h - 1, w - 2, color)
        self.vline(x, y + 1, h - 2, color)
        self.vline(x + w - 1, y + 1, h - 2, color)

    def render(self) -> str:
        lines = []
        for y in range(self.h):
            parts = []
            last = None
            run = ""
            for x in range(self.w):
                key = (self.colors[y][x], self.bold[y][x])
                if key != last:
                    if run:
                        color, bold = last
                        parts.append((BOLD if bold else "") + FG.get(color, FG["white"]) + run)
                    last = key
                    run = self.chars[y][x]
                else:
                    run += self.chars[y][x]
            if run:
                color, bold = last
                parts.append((BOLD if bold else "") + FG.get(color, FG["white"]) + run)
            lines.append("".join(parts) + RESET)
        # tty.setraw() disables the terminal's automatic NL -> CRLF mapping.
        # A bare \n therefore moves down but keeps the old column, making every
        # subsequent row drift right and eventually scroll. Emit CRLF explicitly.
        # Clear + home before each frame so old frames are replaced in-place.
        # Clear the active screen and home the cursor for every frame.
        # The alternate buffer (enabled by Terminal.__enter__) prevents
        # repeated frames from accumulating in the normal scrollback.
        return "\033[H\033[2J" + "\r\n".join(lines)


class Renderer:
    def __init__(self):
        self.stars = [(random.uniform(-19, 19), random.uniform(-12, 12), random.choice(["·", ".", "✧", "˙"])) for _ in range(130)]
        self.anim = 0.0

    def sizes(self):
        # Keep a safety margin: terminals wrap a line that reaches the final
        # column and scroll if a frame writes into the last screen row.
        # Do not force a minimum canvas wider/taller than the real terminal:
        # doing so wraps lines and creates an endless scrollback effect.
        # Leave one column and one row as a safety margin to avoid auto-wrap.
        ts = shutil.get_terminal_size((80, 24))
        cols = max(20, ts.columns - 1)
        rows = max(12, ts.lines - 1)
        return cols, rows

    def world_to_screen(self, x: float, y: float, ox: int, oy: int, mw: int, mh: int):
        sx = (mw - 4) / 46.0
        sy = (mh - 4) / 30.0
        return int(round(ox + mw / 2 + x * sx)), int(round(oy + mh / 2 - y * sy))

    def draw_world(self, c: Canvas, g: Game, ox: int, oy: int, mw: int, mh: int):
        c.box(ox, oy, mw, mh, "darkcyan")
        def plot_world(wx: float, wy: float, glyph: str, color: str, bold: bool = False):
            px0, py0 = self.world_to_screen(wx, wy, ox, oy, mw, mh)
            if ox + 1 <= px0 < ox + mw - 1 and oy + 1 <= py0 < oy + mh - 1:
                c.put(px0, py0, glyph, color, bold)
        # Star field is world anchored for a stable navigation reference.
        for x, y, ch in self.stars:
            plot_world(x, y, ch, "darkblue")
        # Orbit track
        for i in range(360):
            a = math.tau * i / 360
            x, y = 15.0 * math.cos(a), 15.0 * math.sin(a)
            plot_world(x, y, "·", "darkcyan")
        # Secondary orbital reference ring
        for i in range(240):
            a = math.tau * i / 240
            x, y = 18.0 * math.cos(a), 18.0 * math.sin(a)
            plot_world(x, y, "╌", "darkblue")
        # Planet and its atmospheric rim
        pcx, pcy = self.world_to_screen(0, 0, ox, oy, mw, mh)
        sx = (mw - 4) / 46.0
        sy = (mh - 4) / 30.0
        rx, ry = Game.PLANET_R * sx, Game.PLANET_R * sy
        for yy in range(max(oy + 1, int(pcy - ry - 1)), min(oy + mh - 1, int(pcy + ry + 2))):
            for xx in range(max(ox + 1, int(pcx - rx - 1)), min(ox + mw - 1, int(pcx + rx + 2))):
                nx = (xx - pcx) / max(rx, 0.1)
                ny = (yy - pcy) / max(ry, 0.1)
                rr = nx * nx + ny * ny
                if rr <= 1.05:
                    if rr > 0.83:
                        c.put(xx, yy, "·", "cyan")
                    elif rr < 0.18:
                        c.put(xx, yy, "▓", "blue")
                    elif (xx + yy) % 5 == 0:
                        c.put(xx, yy, "▒", "darkcyan")
                    else:
                        c.put(xx, yy, "░", "darkblue")
        c.text(pcx - 3, pcy, "DELTA", "cyan", True, 8)
        # Station symbol
        dx, dy, dvx, dvy, heading = g.station_state()
        px, py = self.world_to_screen(dx, dy, ox, oy, mw, mh)
        if ox + 2 <= px < ox + mw - 2 and oy + 2 <= py < oy + mh - 2:
            c.put(px, py, "◇", "yellow", True)
            c.put(px - 1, py, "[", "orange")
            c.put(px + 1, py, "]", "orange")
            c.put(px, py + 1, "D", "yellow", True)
        # Projectiles
        for b in g.bolts:
            plot_world(b.x, b.y, "•" if b.hostile else "✦", "red" if b.hostile else "cyan", True)
        # Drones
        for d in g.drones:
            px, py = self.world_to_screen(d.x, d.y, ox, oy, mw, mh)
            if ox + 2 <= px < ox + mw - 2 and oy + 1 <= py < oy + mh - 1:
                if d.mode == "TRACK":
                    glyph, color = "◆", "red"
                else:
                    glyph, color = "◇", "magenta"
                if d.flash > 0:
                    color = "white"
                c.put(px - 1, py, "╲", color)
                c.put(px, py, glyph, color, True)
                c.put(px + 1, py, "╱", color)
        # Particles
        for p in g.particles:
            plot_world(p.x, p.y, p.glyph, p.color)
        # Player ship
        s = g.ship
        px, py = self.world_to_screen(s.x, s.y, ox, oy, mw, mh)
        arrows = ["→", "↗", "↑", "↖", "←", "↙", "↓", "↘"]
        idx = int(round((s.angle % math.tau) / (math.pi / 4))) % 8
        color = "white" if s.invuln > 0 and int(g.sim_time * 12) % 2 == 0 else "lime"
        if ox + 2 <= px < ox + mw - 2 and oy + 1 <= py < oy + mh - 1:
            c.put(px - 1, py, "<", "teal", True)
            c.put(px, py, arrows[idx], color, True)
            c.put(px + 1, py, ">", "teal", True)
            if s.shield_on:
                for a in range(0, 360, 45):
                    ax = px + int(round(math.cos(math.radians(a)) * 2))
                    ay = py + int(round(math.sin(math.radians(a)) * 1.3))
                    if ox + 1 <= ax < ox + mw - 1 and oy + 1 <= ay < oy + mh - 1:
                        c.put(ax, ay, "·", "cyan")
        # Target guide line - subtle direction to docking station.
        if g.docked_flash <= 0:
            midx, midy = int((px + self.world_to_screen(dx, dy, ox, oy, mw, mh)[0]) / 2), int((py + self.world_to_screen(dx, dy, ox, oy, mw, mh)[1]) / 2)
            if (g.sim_time * 3) % 1 < 0.5:
                if ox + 1 <= midx < ox + mw - 1 and oy + 1 <= midy < oy + mh - 1:
                    c.put(midx, midy, "┄", "yellow")

    @staticmethod
    def draw_resource_bar(c: Canvas, x: int, y: int, value: float, length: int, color: str):
        ratio = clamp(value / 100.0, 0.0, 1.0)
        filled = int(round(ratio * length))
        chosen = color
        if ratio < 0.25:
            chosen = "red"
        elif ratio < 0.50 and color in ("green", "cyan", "blue"):
            chosen = "yellow"
        for i in range(length):
            c.put(x + i, y, "█" if i < filled else "░", chosen if i < filled else "muted")

    def draw_panel(self, c: Canvas, g: Game, x: int, y: int, w: int, h: int):
        c.box(x, y, w, h, "darkcyan")
        s = g.ship
        dist, rel, ang, _, _, _ = g.nav_data()
        yy = y + 1
        c.text(x + 2, yy, "MODULE / DELTA-07", "cyan", True); yy += 2
        c.hline(x + 1, yy, w - 2, "darkcyan"); yy += 1
        # Compact telemetry: each resource occupies one line to keep the panel tidy.
        c.text(x + 2, yy, f"HULL  {s.hull:5.1f}%", "white"); self.draw_resource_bar(c, x + 14, yy, s.hull, max(5, w - 18), "green"); yy += 1
        c.text(x + 2, yy, f"FUEL  {s.fuel:5.1f}%", "white"); self.draw_resource_bar(c, x + 14, yy, s.fuel, max(5, w - 18), "yellow"); yy += 1
        c.text(x + 2, yy, f"PWR   {s.energy:5.1f}%", "white"); self.draw_resource_bar(c, x + 14, yy, s.energy, max(5, w - 18), "cyan"); yy += 1
        heat_color = "red" if s.heat >= 85 else ("orange" if s.heat >= 60 else "green")
        c.text(x + 2, yy, f"HEAT  {s.heat:5.1f}%", heat_color); self.draw_resource_bar(c, x + 14, yy, s.heat, max(5, w - 18), heat_color); yy += 1
        c.text(x + 2, yy, f"SHLD  {s.shield:5.1f}%", "cyan"); self.draw_resource_bar(c, x + 14, yy, s.shield, max(5, w - 18), "blue"); yy += 1
        c.hline(x + 1, yy, w - 2, "darkcyan"); yy += 1
        c.text(x + 2, yy, "НАВИГАЦИЯ / СТЫКОВКА", "yellow", True); yy += 1
        c.text(x + 2, yy, f"Дистанция     {dist:5.1f} u", "white"); yy += 1
        c.text(x + 2, yy, f"Отн. скорость {rel:5.2f}", "white"); yy += 1
        c.text(x + 2, yy, f"Ориентация    {math.degrees(ang):4.0f}°", "white"); yy += 1
        dist_ok, rel_ok, ang_ok = dist <= 2.2, rel <= 0.68, ang <= math.radians(22)
        c.text(x + 2, yy, f"ДИСТ {'OK' if dist_ok else '--'}  V {'OK' if rel_ok else '--'}  УГОЛ {'OK' if ang_ok else '--'}", "green" if dist_ok and rel_ok and ang_ok else "orange", True); yy += 1
        c.hline(x + 1, yy, w - 2, "darkcyan"); yy += 1
        c.text(x + 2, yy, "СИСТЕМЫ", "cyan", True); yy += 1
        flags = [("ЩИТ", s.shield_on), ("МАЯК", s.beacon_on), ("РАД", s.radiator_on)]
        c.text(x + 2, yy, "  ".join(name + ("●" if on else "○") for name, on in flags), "lime" if any(v for _, v in flags) else "muted", maxw=w - 4); yy += 1
        c.text(x + 2, yy, f"ДРОНЫ {len(g.drones):02d}  СЕКТОР {g.sector:02d}/09", "magenta", True); yy += 1
        c.text(x + 2, yy, f"ОЧКИ {g.score:07d}", "yellow")

    def render_game(self, g: Game) -> str:
        cols, rows = self.sizes()
        c = Canvas(cols, rows)
        # Give the orbital viewport priority over telemetry. On wide terminals
        # the map uses roughly 68% of the available width; the right-side panel
        # stays compact but still wide enough for resource bars and nav values.
        available_w = cols - 3
        if cols >= 130:
            map_w = max(42, min(available_w - 34, int(available_w * 0.68)))
        else:
            map_w = max(36, min(available_w - 30, int(available_w * 0.60)))
        panel_w = cols - map_w - 3
        # Use more of the terminal's vertical space for the actual orbital view.
        map_h = max(12, min(32, rows - 12))
        title = " ORBIT TERMINAL  /  MODULE DELTA "
        c.text(2, 0, title, "cyan", True)
        c.text(cols - 28, 0, "СИМУЛЯЦИЯ // В РЕАЛЬНОМ ВРЕМЕНИ", "muted")
        c.text(2, 1, f"СЕКТОР {g.sector:02d}  •  ПРОТОКОЛ СТЫКОВКИ  •  {time.strftime('%H:%M:%S')}", "yellow")
        c.text(2, 2, "ИНЕРЦИАЛЬНЫЙ РЕЖИМ  |  ГРАВИТАЦИОННОЕ ПОЛЕ  |  РУЧНОЕ УПРАВЛЕНИЕ", "muted")
        # thin heading line
        c.hline(1, 3, cols - 2, "darkcyan")
        ox, oy = 1, 4
        self.draw_world(c, g, ox, oy, map_w, map_h)
        self.draw_panel(c, g, ox + map_w + 1, oy, panel_w, map_h)
        footer_y = oy + map_h
        c.hline(1, footer_y, cols - 2, "darkcyan")
        log_y = footer_y + 1
        logs = g.logs[:2]
        for i, item in enumerate(logs):
            c.text(2, log_y + i, "› " + item.text, item.color, i == 0, cols - 4)
        controls_y = min(rows - 2, log_y + 3)
        c.text(2, controls_y, "W/↑ ИМПУЛЬС  S/↓ ТОРМОЗ  A/D ПОВОРОТ  Q/E БОКОВОЙ ИМПУЛЬС  SPACE ЭМИТТЕР", "cyan", True, cols - 4)
        if controls_y + 1 < rows:
            c.text(2, controls_y + 1, "F СТЫКОВКА  X ЩИТ  R МАЯК  C РАДИАТОР  P ПАУЗА  H СПРАВКА  ESC МЕНЮ", "muted", False, cols - 4)
        if g.paused:
            self.center_overlay(c, "ПАУЗА", "СИМУЛЯЦИЯ ОСТАНОВЛЕНА  •  P — ПРОДОЛЖИТЬ", "yellow")
        if g.docked_flash > 0:
            self.center_overlay(c, "СТЫКОВКА УСПЕШНА", f"СЕКТОР {g.sector:02d}  •  МОДУЛЬ ОБСЛУЖЕН", "green")
        if g.game_over:
            self.center_overlay(c, "МОДУЛЬ ПОТЕРЯН", f"ОЧКИ {g.score:07d}  •  ENTER — ПОВТОРИТЬ  •  ESC — МЕНЮ", "red")
        if g.victory:
            self.center_overlay(c, "МИССИЯ ВЫПОЛНЕНА", f"9 СЕКТОРОВ ПРОЙДЕНО  •  ОЧКИ {g.score:07d}  •  ENTER — СНОВА", "green")
        return c.render()

    @staticmethod
    def center_overlay(c: Canvas, title: str, subtitle: str, color: str):
        y = c.h // 2 - 1
        width = max(len(title), len(subtitle)) + 6
        x = max(1, (c.w - width) // 2)
        c.box(x, y - 1, width, 4, color)
        c.text(x + (width - len(title)) // 2, y, title, color, True)
        c.text(x + (width - len(subtitle)) // 2, y + 1, subtitle, "white", True)

    def render_launcher(self, selected: int, screen: str = "menu") -> str:
        cols, rows = self.sizes()
        c = Canvas(cols, rows)
        cx = cols // 2
        if cols < 76 or rows < 26:
            # Compact layout for narrow VS Code / Mac Terminal panes.
            c.text(2, 1, "ORBIT TERMINAL / DELTA", "cyan", True, cols - 4)
            c.hline(1, 2, cols - 2, "darkcyan")
            c.text(2, 4, "ОРБИТАЛЬНЫЙ СИМУЛЯТОР", "yellow", True, cols - 4)
            c.text(2, 5, "MODULE DELTA-07  |  HELIOS-9", "teal", False, cols - 4)
            c.text(2, 7, "СТАТУС: READY  |  КАНАЛ: SECURE", "green", False, cols - 4)
            if screen == "help":
                help_lines = [
                    "УПРАВЛЕНИЕ:",
                    "W/↑ — импульс вперёд; S/↓ — тормоз", 
                    "A/D или ←/→ — поворот; Q/E — боковой импульс",
                    "SPACE — эмиттер; F — стыковка",
                    "X — щит; R — маяк; C — радиатор",
                    "P — пауза; ESC — назад",
                ]
                start_y = 9
                for i, line in enumerate(help_lines):
                    yy = start_y + i
                    if yy < rows - 3:
                        c.text(2, yy, line, "yellow" if i == 0 else "white", i == 0, cols - 4)
                c.text(2, rows - 2, "ESC или ENTER — НАЗАД", "cyan", False, cols - 4)
            else:
                options = ["НАЧАТЬ МИССИЮ", "УПРАВЛЕНИЕ / ОБУЧЕНИЕ", "ВЫХОД"]
                y0 = min(10, max(8, rows - 8))
                for i, item in enumerate(options):
                    yy = y0 + i * 2
                    if yy < rows - 3:
                        mark = "▶" if i == selected else " "
                        c.text(2, yy, f"{mark} {item}", "lime" if i == selected else "muted", i == selected, cols - 4)
                c.text(2, rows - 2, "W/S — ВЫБОР  |  ENTER — ПОДТВЕРДИТЬ", "cyan", False, cols - 4)
            return c.render()

        c.text(max(2, cx - 25), 2, "╔══════════════════════════════════════════════════════════╗", "darkcyan")
        c.text(max(2, cx - 25), 3, "║       O R B I T   T E R M I N A L   /   D E L T A       ║", "cyan", True)
        c.text(max(2, cx - 25), 4, "╚══════════════════════════════════════════════════════════╝", "darkcyan")
        c.text(max(2, cx - 22), 6, "ОРБИТАЛЬНЫЙ СИМУЛЯТОР  •  MODULE DELTA-07", "yellow", True)
        c.text(max(2, cx - 25), 8, "╭────────────────────────────────────────────────────────╮", "darkcyan")
        c.text(max(2, cx - 25), 9, "│  СТАНЦИЯ: HELIOS-9          СТАТУС: READY               │", "teal")
        c.text(max(2, cx - 25), 10, "│  ПИЛОТ: НЕ НАЗНАЧЕН        КАНАЛ: SECURE               │", "teal")
        c.text(max(2, cx - 25), 11, "╰────────────────────────────────────────────────────────╯", "darkcyan")
        if screen == "help":
            items = [
                ("ПОЛЁТ И ИНЕРЦИЯ", "Модуль сохраняет скорость. Короткие импульсы меняют траекторию."),
                ("W / ↑", "Импульс вперёд по направлению носа корабля."),
                ("S / ↓", "Ретро-импульс: замедление вдоль направления носа."),
                ("A / D или ← / →", "Поворот носа на 11 градусов за нажатие."),
                ("Q / E", "Боковые корректирующие импульсы."),
                ("SPACE", "Энергетический эмиттер для защиты от дронов."),
                ("F", "Запрос стыковки: близко, малая относительная скорость, верный угол."),
                ("X / R / C", "Щит / транспондер-маяк / радиатор."),
                ("ПОДСКАЗКА", "Включённый маяк и перегрев повышают тепловую заметность."),
                ("НАЗАД", "Нажми ESC или ENTER."),
            ]
            y = 14
            for i, (a, b) in enumerate(items):
                c.text(max(4, cx - 29), y + i * 1, f"{a:<22} {b}", "yellow" if i in (0, 8) else "white", i in (0, 8), cols - 8)
            c.text(max(2, cx - 25), rows - 3, "HELIOС-9 // FLIGHT MANUAL REV. 4.2", "muted")
        else:
            options = ["НАЧАТЬ МИССИЮ", "УПРАВЛЕНИЕ / ОБУЧЕНИЕ", "ВЫХОД"]
            y0 = 15
            for i, item in enumerate(options):
                mark = "▶" if i == selected else " "
                color = "lime" if i == selected else "muted"
                c.text(cx - 13, y0 + i * 2, f"{mark}  {item}", color, i == selected)
            c.text(max(2, cx - 24), rows - 4, "W/S или ↑/↓ — ВЫБОР     ENTER — ПОДТВЕРДИТЬ", "cyan")
            c.text(max(2, cx - 24), rows - 2, "PYTHON TERMINAL SIMULATION  //  BUILD 1.0.2", "muted")
        return c.render()


# ---------- Main loop ----------
def show_help_overlay(renderer: Renderer) -> str:
    return renderer.render_launcher(0, "help")


def run_game():
    renderer = Renderer()
    menu_selected = 0
    help_screen = False
    with Terminal() as term:
        while True:
            if help_screen:
                sys.stdout.write(renderer.render_launcher(menu_selected, "help"))
                sys.stdout.flush()
                keys = term.read_keys()
                if "esc" in keys or "enter" in keys or "h" in keys:
                    help_screen = False
                time.sleep(0.05)
                continue
            sys.stdout.write(renderer.render_launcher(menu_selected, "menu"))
            sys.stdout.flush()
            keys = term.read_keys()
            for k in keys:
                if k in ("w", "up"):
                    menu_selected = (menu_selected - 1) % 3
                elif k in ("s", "down"):
                    menu_selected = (menu_selected + 1) % 3
                elif k == "h":
                    help_screen = True
                elif k == "enter":
                    if menu_selected == 0:
                        game = Game()
                        play_loop(term, renderer, game)
                    elif menu_selected == 1:
                        help_screen = True
                    else:
                        return
                elif k in ("q", "esc"):
                    return
            time.sleep(0.06)


def play_loop(term: Terminal, renderer: Renderer, g: Game):
    last = time.perf_counter()
    help_overlay = False
    while True:
        now = time.perf_counter()
        dt = min(0.12, max(0.01, now - last))
        last = now
        keys = term.read_keys()
        if help_overlay:
            sys.stdout.write(renderer.render_launcher(0, "help"))
            sys.stdout.flush()
            if any(k in ("esc", "h", "enter") for k in keys):
                help_overlay = False
            time.sleep(0.05)
            continue
        # Discrete impulse controls. Holding a key lets terminal key-repeat send pulses.
        for k in keys:
            if k == "esc":
                return
            if k == "p":
                g.paused = not g.paused
                g.add_log("ПАУЗА" if g.paused else "СИМУЛЯЦИЯ ПРОДОЛЖЕНА", "yellow")
            elif k == "h":
                help_overlay = True
            elif g.game_over or g.victory:
                if k == "enter":
                    if g.victory:
                        return
                    fresh = Game()
                    g.__dict__.update(fresh.__dict__)
                continue
            elif k in ("a", "left"):
                g.rotate(1)
            elif k in ("d", "right"):
                g.rotate(-1)
            elif k in ("w", "up"):
                g.thrust(1)
            elif k in ("s", "down"):
                g.thrust(-1)
            elif k == "q":
                g.lateral(-1)
            elif k == "e":
                g.lateral(1)
            elif k == "space":
                g.fire()
            elif k == "f":
                g.attempt_dock()
            elif k == "x":
                g.toggle_shield()
            elif k == "r":
                g.toggle_beacon()
            elif k == "c":
                g.toggle_radiator()
        g.update(dt)
        sys.stdout.write(renderer.render_game(g))
        sys.stdout.flush()
        # ~15 FPS, low CPU use; input is still checked every frame.
        time.sleep(0.055)


def main():
    if sys.platform == "win32":
        os.system("")  # enable ANSI on modern Windows terminals where possible
    try:
        run_game()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # Terminal context restores the user's shell before this message is printed.
        print("\nОшибка запуска:", exc)
        print("Проверь, что запускаешь файл в интерактивном Terminal, а не в окне вывода Code Runner.")


if __name__ == "__main__":
    main()
