#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ORBITAL DEFENSE
A colorful terminal bullet-hell arcade game written with the Python standard library.

Run: python3 orbital_defense_v22.py
Controls are shown in the title screen and at the bottom of the game.
"""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TITLE = "ОРБИТАЛЬНАЯ ОБОРОНА"
VERSION = "2.3"
FPS = 30
FRAME_TIME = 1.0 / FPS
MIN_WIDTH = 66
MIN_HEIGHT = 22

# ANSI colors. These work in modern Windows Terminal, macOS Terminal and most
# Linux terminals. The game falls back to plain text when output is redirected.
RESET = "\033[0m"
CLEAR = "\033[2J"
HOME = "\033[H"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
ALT_SCREEN_ON = "\033[?1049h"
ALT_SCREEN_OFF = "\033[?1049l"

C = {
    "white": 97,
    "gray": 37,
    "dim": 90,
    "red": 91,
    "orange": 33,
    "yellow": 93,
    "green": 92,
    "cyan": 96,
    "blue": 94,
    "magenta": 95,
    "pink": 95,
    "darkred": 31,
    "darkgreen": 32,
    "darkcyan": 36,
    "darkblue": 34,
    "purple": 35,
}

WEAPONS = ("ПУЛЕМЁТ", "ИОННЫЙ ЛАЗЕР", "РАКЕТЫ")
WEAPON_INFO = (
    "Быстрая стрельба",
    "Пробивает несколько целей",
    "Самонаводящиеся ракеты",
)


# ---------------------------------------------------------------------------
# Small terminal compatibility layer
# ---------------------------------------------------------------------------

class Terminal:
    """Put the terminal into a temporary interactive mode and read keys."""

    def __init__(self) -> None:
        self.is_tty = bool(sys.stdin.isatty() and sys.stdout.isatty())
        self._old_termios = None
        self._fd = None
        self._win = os.name == "nt"
        self._vt_enabled = False

    def __enter__(self) -> "Terminal":
        if not self.is_tty:
            return self
        if self._win:
            self._enable_windows_vt()
        else:
            try:
                import termios
                import tty
                self._fd = sys.stdin.fileno()
                self._old_termios = termios.tcgetattr(self._fd)
                tty.setcbreak(self._fd)
            except Exception:
                self.is_tty = False
                return self
        sys.stdout.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR + HOME)
        sys.stdout.flush()
        return self

    def _enable_windows_vt(self) -> None:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                self._vt_enabled = bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
        except Exception:
            self._vt_enabled = False

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.is_tty:
            sys.stdout.write(RESET + SHOW_CURSOR + ALT_SCREEN_OFF)
            sys.stdout.flush()
        if not self._win and self._old_termios is not None and self._fd is not None:
            try:
                import termios
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_termios)
            except Exception:
                pass

    def read_keys(self) -> list[str]:
        """Return all key events currently waiting, without blocking."""
        if not self.is_tty:
            return []
        if self._win:
            return self._read_windows_keys()
        return self._read_posix_keys()

    @staticmethod
    def _read_windows_keys() -> list[str]:
        import msvcrt
        keys: list[str] = []
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                if msvcrt.kbhit():
                    code = msvcrt.getwch()
                    keys.append({"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(code, ""))
            elif ch == "\r":
                keys.append("ENTER")
            elif ch == "\x1b":
                keys.append("ESC")
            else:
                keys.append(ch.lower() if ch.isalpha() else ch)
        return [k for k in keys if k]

    def _read_posix_keys(self) -> list[str]:
        import select
        keys: list[str] = []
        fd = sys.stdin.fileno()
        while select.select([fd], [], [], 0)[0]:
            try:
                data = os.read(fd, 32)
            except OSError:
                break
            if not data:
                break
            i = 0
            while i < len(data):
                ch = data[i]
                if ch == 27:
                    # Arrow keys arrive as ESC [ A/B/C/D.
                    if i + 2 < len(data) and data[i + 1] == 91:
                        keys.append({65: "UP", 66: "DOWN", 67: "RIGHT", 68: "LEFT"}.get(data[i + 2], "ESC"))
                        i += 3
                        continue
                    keys.append("ESC")
                elif ch in (10, 13):
                    keys.append("ENTER")
                else:
                    try:
                        letter = bytes([ch]).decode("utf-8").lower()
                    except UnicodeDecodeError:
                        letter = ""
                    if letter:
                        keys.append(letter)
                i += 1
        return keys


# ---------------------------------------------------------------------------
# Drawing system: a tiny in-memory framebuffer, then one terminal write/frame
# ---------------------------------------------------------------------------

class Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.cells: list[list[tuple[str, int]]] = []
        self.clear()

    def clear(self) -> None:
        self.cells = [[(" ", C["gray"]) for _ in range(self.width)] for _ in range(self.height)]

    def put(self, x: int, y: int, ch: str, color: int = C["white"]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height and ch:
            # Keep one terminal cell per symbol; avoid multi-column emoji.
            self.cells[y][x] = (ch[0], color)

    def text(self, x: int, y: int, value: str, color: int = C["white"], max_width: int | None = None) -> None:
        if y < 0 or y >= self.height:
            return
        limit = len(value) if max_width is None else min(len(value), max_width)
        for i in range(limit):
            self.put(x + i, y, value[i], color)

    def centered(self, y: int, value: str, color: int = C["white"]) -> None:
        self.text(max(0, (self.width - len(value)) // 2), y, value, color, self.width)

    def bar(self, x: int, y: int, width: int, value: float, maximum: float,
            fill_color: int, empty_color: int = C["dim"]) -> None:
        if width <= 0:
            return
        ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
        filled = int(round(width * ratio))
        for i in range(width):
            self.put(x + i, y, "█" if i < filled else "░", fill_color if i < filled else empty_color)

    def render(self) -> str:
        out: list[str] = [HOME]
        for row in self.cells:
            current_color = None
            line: list[str] = []
            for ch, color in row:
                if color != current_color:
                    line.append(f"\033[{color}m")
                    current_color = color
                line.append(ch)
            line.append(RESET)
            out.append("".join(line))
        return "\n".join(out)


# ---------------------------------------------------------------------------
# Game objects
# ---------------------------------------------------------------------------

@dataclass
class Player:
    x: float
    y: float
    hp: int = 5
    max_hp: int = 5
    shield: float = 100.0
    shield_on: bool = False
    heat: float = 0.0
    overheated: bool = False
    weapon: int = 0
    fire_cd: float = 0.0
    trigger_time: float = 0.0
    invulnerable: float = 0.0
    rapid_timer: float = 0.0
    damage_timer: float = 0.0
    spread_timer: float = 0.0


@dataclass
class Projectile:
    x: float
    y: float
    vx: float
    vy: float
    damage: float
    owner: str
    glyph: str
    color: int
    life: float = 4.0
    pierce: int = 0
    homing: bool = False
    splash: float = 0.0


@dataclass
class Enemy:
    x: float
    y: float
    kind: str
    hp: float
    max_hp: float
    vx: float
    vy: float
    fire_cd: float = 1.0
    age: float = 0.0
    phase: float = 0.0


@dataclass
class Meteor:
    x: float
    y: float
    vx: float
    vy: float
    hp: float = 2.0
    spin: float = 0.0


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    glyph: str
    color: int
    life: float
    max_life: float


@dataclass
class PowerUp:
    x: float
    y: float
    kind: str
    vy: float = 2.4
    age: float = 0.0


@dataclass
class Star:
    x: float
    y: float
    speed: float
    glyph: str
    color: int


@dataclass
class Boss:
    x: float
    y: float
    hp: float
    max_hp: float
    phase: int = 1
    age: float = 0.0
    attack_cd: float = 1.2
    hit_flash: float = 0.0


# ---------------------------------------------------------------------------
# Main game
# ---------------------------------------------------------------------------

class OrbitalDefense:
    def __init__(self) -> None:
        self.width, self.height = shutil.get_terminal_size((100, 30))
        self.canvas = Canvas(self.width, self.height)
        self.rng = random.Random()
        self.state = "menu"
        self.running = True
        self.time_alive = 0.0
        self.score = 0
        self.high_score = self._load_high_score()
        self.wave = 1
        self.player: Player | None = None
        self.enemies: list[Enemy] = []
        self.projectiles: list[Projectile] = []
        self.meteors: list[Meteor] = []
        self.particles: list[Particle] = []
        self.powerups: list[PowerUp] = []
        self.stars: list[Star] = []
        self.boss: Boss | None = None
        self.spawn_left = 0
        self.spawn_cd = 0.0
        self.meteor_left = 0
        self.wave_clear_timer = 0.0
        self.meteor_cd = 0.0
        self.powerup_spawn_timer = self.rng.uniform(11.0, 16.0)
        self.message = ""
        self.message_timer = 0.0
        self.menu_selection = 0
        self._make_stars()

    # ----- persistence -----------------------------------------------------

    @staticmethod
    def _score_path() -> Path:
        return Path.home() / ".orbital_defense_score.json"

    def _load_high_score(self) -> int:
        try:
            data = json.loads(self._score_path().read_text(encoding="utf-8"))
            return max(0, int(data.get("high_score", 0)))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return 0

    def _save_high_score(self) -> None:
        try:
            self._score_path().write_text(
                json.dumps({"high_score": self.high_score}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            # The game remains playable even if the home folder is read-only.
            pass

    # ----- screen and world setup -----------------------------------------

    def _resize_if_needed(self) -> None:
        w, h = shutil.get_terminal_size((100, 30))
        if (w, h) != (self.width, self.height):
            self.width, self.height = w, h
            self.canvas = Canvas(w, h)
            if self.player:
                self.player.x = max(3, min(w - 4, self.player.x))
                self.player.y = h - 5
            self._make_stars()

    @property
    def top(self) -> int:
        return 3

    @property
    def bottom(self) -> int:
        return self.height - 4

    def _make_stars(self) -> None:
        count = max(35, int(self.width * self.height * 0.035))
        self.stars = []
        for _ in range(count):
            layer = self.rng.random()
            if layer < 0.55:
                speed, glyph, color = 2.3, ".", C["dim"]
            elif layer < 0.88:
                speed, glyph, color = 5.0, "+", C["blue"]
            else:
                speed, glyph, color = 8.0, "*", C["cyan"]
            self.stars.append(Star(self.rng.randrange(max(1, self.width)),
                                   self.rng.randrange(max(1, self.height)),
                                   speed, glyph, color))

    def _reset_game(self) -> None:
        self.score = 0
        self.time_alive = 0.0
        self.wave = 1
        self.enemies.clear()
        self.projectiles.clear()
        self.meteors.clear()
        self.particles.clear()
        self.powerups.clear()
        self.boss = None
        self.player = Player(self.width / 2, self.bottom - 1, hp=12, max_hp=12)
        self.spawn_left = 0
        self.spawn_cd = 0.0
        self.meteor_left = 0
        self.wave_clear_timer = 0.0
        self.meteor_cd = 2.0
        self.powerup_spawn_timer = self.rng.uniform(11.0, 16.0)
        self._start_wave()
        self.state = "playing"

    def _start_wave(self) -> None:
        self.wave_clear_timer = 0.0
        if self.wave % 5 == 0:
            max_hp = 285 + self.wave * 24
            self.boss = Boss(self.width / 2, self.top + 9, max_hp, max_hp)
            self.spawn_left = 0
            self._announce(f"!!! ВНИМАНИЕ: БОСС СЕКТОРА {self.wave} !!!", 4.8)
        else:
            self.spawn_left = min(4 + self.wave // 2, 8)
            self.spawn_cd = 1.2
            self._announce(f"ВОЛНА {self.wave} // ПРИБЛИЖАЮТСЯ ПРОТИВНИКИ", 2.4)
            if self.wave % 2 == 0:
                self.meteor_left = min(2, 1 + self.wave // 6)
                self.meteor_cd = 2.4
            else:
                self.meteor_left = 0

    def _announce(self, text: str, duration: float = 2.0) -> None:
        self.message = text
        self.message_timer = duration

    # ----- input -----------------------------------------------------------

    def handle_keys(self, keys: Iterable[str]) -> None:
        for key in keys:
            if key in ("q", "ESC"):
                if self.state == "menu":
                    self.running = False
                elif self.state in ("gameover", "victory"):
                    self.state = "menu"
                else:
                    self.state = "menu"
                continue

            if self.state == "menu":
                if key in ("UP", "w"):
                    self.menu_selection = (self.menu_selection - 1) % 2
                elif key in ("DOWN", "s"):
                    self.menu_selection = (self.menu_selection + 1) % 2
                elif key in ("ENTER", " "):
                    if self.menu_selection == 0:
                        self._reset_game()
                    else:
                        self.running = False
                continue

            if self.state == "playing":
                p = self.player
                if p is None:
                    continue
                if key in ("LEFT", "a"):
                    p.x -= 4.5
                elif key in ("RIGHT", "d"):
                    p.x += 4.5
                elif key in ("UP", "w"):
                    p.y -= 2.0
                elif key in ("DOWN", "s"):
                    p.y += 2.0
                elif key == " ":
                    # A space press starts a short firing burst. Holding the key
                    # renews the burst through the terminal's key-repeat events.
                    p.trigger_time = max(p.trigger_time, 0.18)
                elif key in ("1", "2", "3"):
                    p.weapon = int(key) - 1
                    self._announce(f"ОРУЖИЕ: {WEAPONS[p.weapon]}", 1.0)
                elif key == "x":
                    if p.shield > 1:
                        p.shield_on = not p.shield_on
                        self._announce("ЭНЕРГОЩИТ ВКЛЮЧЁН" if p.shield_on else "ЭНЕРГОЩИТ ВЫКЛЮЧЕН", 0.8)
                elif key == "p":
                    self.state = "paused"
                elif key == "h":
                    self._announce("WASD/СТРЕЛКИ — ДВИЖЕНИЕ | ПРОБЕЛ — СТРЕЛЬБА (ДЕРЖАТЬ) | 1–3 — ОРУЖИЕ | X — ЩИТ", 3.0)
            elif self.state == "paused":
                if key in ("p", "ENTER", " "):
                    self.state = "playing"
                elif key == "m":
                    self.state = "menu"
            elif self.state in ("gameover", "victory"):
                if key in ("ENTER", " "):
                    self._reset_game()
                elif key == "m":
                    self.state = "menu"

    # ----- spawning --------------------------------------------------------

    def _spawn_enemy(self) -> None:
        x = self.rng.uniform(5, max(6, self.width - 6))
        roll = self.rng.random()
        if self.wave >= 3 and roll < 0.11:
            kind, hp, vx, vy = "tank", 12.0, self.rng.choice((-1.0, 1.0)) * 0.8, 1.0
        elif self.wave >= 2 and roll < 0.38:
            kind, hp, vx, vy = "zigzag", 5.0, self.rng.choice((-1.0, 1.0)) * 1.25, 1.55
        else:
            kind, hp, vx, vy = "scout", 3.0, self.rng.choice((-1.0, 1.0)) * 0.95, 1.8
        self.enemies.append(Enemy(x, float(self.top - 4), kind, hp, hp, vx,
                                  vy + min(0.35, self.wave * 0.018),
                                  fire_cd=self.rng.uniform(3.8, 5.6),
                                  phase=self.rng.uniform(0, math.tau)))

    def _spawn_meteor(self) -> None:
        self.meteors.append(Meteor(
            self.rng.uniform(3, max(4, self.width - 4)),
            float(self.top - 1),
            self.rng.uniform(-1.4, 1.4),
            self.rng.uniform(4.0, 5.8 + min(1.0, self.wave * 0.05)),
            hp=2.0,
            spin=self.rng.random() * math.tau,
        ))

    def _spawn_powerup(self) -> None:
        """Send a rare independent supply capsule in from the top of the screen."""
        if self.powerups:
            return
        kind = self.rng.choices(
            ["health", "coolant", "shield", "rapid", "damage", "spread", "score"],
            [17, 15, 17, 16, 14, 13, 8],
        )[0]
        x = self.rng.uniform(8, max(9, self.width - 9))
        self.powerups.append(PowerUp(x, float(self.top - 5), kind, vy=self.rng.uniform(2.0, 2.7)))
        self._announce("СИГНАЛ: КАПСУЛА СНАБЖЕНИЯ ВХОДИТ В АТМОСФЕРУ", 2.6)

    def _grant_powerup(self, item: PowerUp) -> None:
        """Open a collected supply crate and apply its randomized reward."""
        p = self.player
        if p is None:
            return
        labels = {
            "health": "АПТЕЧКА: +2 КОРПУСА",
            "coolant": "КРИОКАПСУЛА: ОРУДИЯ ОХЛАЖДЕНЫ",
            "shield": "ЭНЕРГОЯЧЕЙКА: ЩИТ +65%",
            "rapid": "ТУРБОЗАРЯД: СКОРОСТРЕЛЬНОСТЬ +10 СЕК",
            "damage": "УСИЛИТЕЛЬ: УРОН x1.7 НА 10 СЕК",
            "spread": "РАСЩЕПИТЕЛЬ: ТРОЙНОЙ ЗАЛП НА 10 СЕК",
            "score": "АРТЕФАКТ: +500 ОЧКОВ",
        }
        if item.kind == "health":
            p.hp = min(p.max_hp, p.hp + 2)
        elif item.kind == "coolant":
            p.heat = 0.0
            p.overheated = False
        elif item.kind == "shield":
            p.shield = min(100.0, p.shield + 65.0)
        elif item.kind == "rapid":
            p.rapid_timer = max(p.rapid_timer, 10.0)
        elif item.kind == "damage":
            p.damage_timer = max(p.damage_timer, 10.0)
        elif item.kind == "spread":
            p.spread_timer = max(p.spread_timer, 10.0)
        elif item.kind == "score":
            self.score += 500
        self.score += 25
        self._explode(item.x, item.y, 22, C["cyan"])
        self._announce(labels.get(item.kind, "ГРУЗ СОБРАН"), 2.0)

    # ----- effects and damage ---------------------------------------------

    def _explode(self, x: float, y: float, amount: int = 12, color: int | None = None) -> None:
        palette = [C["yellow"], C["orange"], C["red"], C["white"]]
        for _ in range(amount):
            angle = self.rng.random() * math.tau
            speed = self.rng.uniform(2.0, 8.0)
            col = color if color is not None else self.rng.choice(palette)
            glyph = self.rng.choice(("*", "+", ".", "x"))
            life = self.rng.uniform(0.25, 0.8)
            self.particles.append(Particle(x, y, math.cos(angle) * speed,
                                           math.sin(angle) * speed, glyph, col, life, life))

    def _damage_player(self, amount: int = 1) -> None:
        p = self.player
        if p is None or p.invulnerable > 0:
            return
        if p.shield_on and p.shield > 0:
            p.shield = max(0.0, p.shield - 18.0 * amount)
            if p.shield <= 0:
                p.shield_on = False
                self._announce("ЭНЕРГОЩИТ ИСЧЕРПАН", 1.2)
            self._explode(p.x, p.y, 5, C["cyan"])
            return
        p.hp -= amount
        p.invulnerable = 1.8
        self._explode(p.x, p.y, 18, C["red"])
        if p.hp <= 0:
            p.hp = 0
            self.state = "gameover"
            self.high_score = max(self.high_score, self.score)
            self._save_high_score()

    def _damage_enemy(self, enemy: Enemy, amount: float) -> None:
        enemy.hp -= amount
        if enemy.hp <= 0:
            self.score += {"scout": 100, "zigzag": 160, "tank": 300}.get(enemy.kind, 100)
            self._explode(enemy.x, enemy.y, 14 if enemy.kind != "tank" else 24)
            if self.score > self.high_score:
                self.high_score = self.score

    def _damage_boss(self, amount: float) -> None:
        boss = self.boss
        if boss is None:
            return
        boss.hp -= amount
        boss.hit_flash = 0.08
        if boss.phase == 1 and boss.hp <= boss.max_hp * 0.67:
            boss.phase = 2
            self._announce("БОСС: ФАЗА 2 // УСИЛЕННАЯ ЗАЩИТА", 2.8)
            self._explode(boss.x, boss.y + 2, 28, C["magenta"])
        elif boss.phase == 2 and boss.hp <= boss.max_hp * 0.34:
            boss.phase = 3
            self._announce("БОСС: ФИНАЛЬНАЯ ФАЗА!", 3.0)
            self._explode(boss.x, boss.y + 2, 34, C["red"])
        if boss.hp <= 0:
            self.score += 5000
            self._explode(boss.x, boss.y + 2, 70, C["yellow"])
            self.boss = None
            if self.wave >= 20:
                self.state = "victory"
                self._announce("ПОСЛЕДНИЙ БОСС УНИЧТОЖЕН! СЕКТОР СПАСЁН!", 5.0)
                self.high_score = max(self.high_score, self.score)
                self._save_high_score()
            else:
                # The commanders recur every five sectors; reward the player
                # with a modest repair so the campaign can continue.
                if self.player is not None:
                    self.player.hp = min(self.player.max_hp, self.player.hp + 3)
                    self.player.shield = min(100.0, self.player.shield + 35.0)
                self._announce("БОСС УНИЧТОЖЕН! +3 КОРПУСА, +35% ЩИТА", 3.5)

    # ----- weapon system ---------------------------------------------------

    def _fire_weapon(self, dt: float) -> None:
        p = self.player
        if p is None or p.trigger_time <= 0:
            return
        if p.overheated:
            if p.heat <= 34:
                p.overheated = False
                self._announce("ОРУДИЯ ОСТЫЛИ", 0.8)
            else:
                return
        if p.fire_cd > 0:
            return

        damage_boost = 1.7 if p.damage_timer > 0 else 1.0
        rapid = 0.62 if p.rapid_timer > 0 else 1.0
        if p.weapon == 0:
            # Twin cannons; a collected splitter temporarily turns them into a triple shot.
            offsets = (-2.0, 0.0, 2.0) if p.spread_timer > 0 else (-1.0, 1.0)
            for offset in offsets:
                self.projectiles.append(Projectile(p.x + offset, p.y - 1, 0, -42, 0.9 * damage_boost,
                                                   "player", "|", C["cyan"], life=2.5))
            p.heat += 5.0
            p.fire_cd = 0.075 * rapid
        elif p.weapon == 1:
            self.projectiles.append(Projectile(p.x, p.y - 1, 0, -52, 3.2 * damage_boost,
                                               "player", "║", C["magenta"], life=2.0, pierce=3))
            p.heat += 18.0
            p.fire_cd = 0.30 * rapid
        else:
            target = self._nearest_target(p.x, p.y)
            vx = 0.0 if target is None else max(-3.6, min(3.6, (target.x - p.x) * 1.1))
            self.projectiles.append(Projectile(p.x, p.y - 1, vx, -16.0, 5.0 * damage_boost,
                                               "player", "◆", C["yellow"], life=4.0,
                                               homing=True, splash=3.2))
            p.heat += 13.0
            p.fire_cd = 0.62 * rapid

        if p.heat >= 100:
            p.heat = 100
            p.overheated = True
            self._announce("ПЕРЕГРЕВ! ДАЙТЕ ОРУДИЯМ ОСТЫТЬ", 1.4)

    def _nearest_target(self, x: float, y: float) -> Enemy | Boss | None:
        candidates: list[Enemy | Boss] = list(self.enemies)
        if self.boss is not None:
            candidates.append(self.boss)
        if not candidates:
            return None
        return min(candidates, key=lambda obj: (obj.x - x) ** 2 + (obj.y - y) ** 2)

    # ----- update loop -----------------------------------------------------

    def update(self, dt: float) -> None:
        self._resize_if_needed()
        self.time_alive += dt
        self._update_stars(dt)
        if self.message_timer > 0:
            self.message_timer = max(0.0, self.message_timer - dt)

        if self.state != "playing":
            return
        p = self.player
        if p is None:
            return

        # Keep the ship inside the playable field.
        p.x = max(3.0, min(self.width - 4.0, p.x))
        p.y = max(self.top + 5.0, min(self.bottom - 1.0, p.y))
        p.invulnerable = max(0.0, p.invulnerable - dt)
        p.trigger_time = max(0.0, p.trigger_time - dt)
        p.rapid_timer = max(0.0, p.rapid_timer - dt)
        p.damage_timer = max(0.0, p.damage_timer - dt)
        p.spread_timer = max(0.0, p.spread_timer - dt)
        p.heat = max(0.0, p.heat - (12.0 if p.weapon == 1 else 20.0 if p.weapon == 0 else 16.0) * dt)
        p.fire_cd = max(0.0, p.fire_cd - dt)

        if p.shield_on:
            p.shield = max(0.0, p.shield - 14.0 * dt)
            if p.shield <= 0:
                p.shield_on = False
                self._announce("ЭНЕРГОЩИТ ИСЧЕРПАН", 1.0)
        else:
            p.shield = min(100.0, p.shield + 14.0 * dt)

        self._fire_weapon(dt)
        self._update_spawning(dt)
        self._update_enemies(dt)
        self._update_boss(dt)
        self._update_meteors(dt)
        self._update_projectiles(dt)
        self._update_powerups(dt)
        self._update_particles(dt)
        self._collisions()
        self._check_wave_clear(dt)

    def _update_stars(self, dt: float) -> None:
        for star in self.stars:
            star.y += star.speed * dt
            if star.y >= self.height:
                star.y = 0
                star.x = self.rng.randrange(max(1, self.width))

    def _update_spawning(self, dt: float) -> None:
        if self.boss is not None:
            return
        if self.spawn_left > 0:
            self.spawn_cd -= dt
            if self.spawn_cd <= 0:
                self._spawn_enemy()
                self.spawn_left -= 1
                self.spawn_cd = max(1.0, 1.65 - self.wave * 0.012)
        if self.meteor_left > 0:
            self.meteor_cd -= dt
            if self.meteor_cd <= 0:
                self._spawn_meteor()
                self.meteor_left -= 1
                self.meteor_cd = self.rng.uniform(2.2, 3.0)

    def _update_enemies(self, dt: float) -> None:
        p = self.player
        for enemy in self.enemies[:]:
            enemy.age += dt
            enemy.y += enemy.vy * dt
            if enemy.kind == "zigzag":
                enemy.x += math.sin(enemy.age * 4.0 + enemy.phase) * 7.0 * dt
            else:
                enemy.x += enemy.vx * dt
            if enemy.x < 3 or enemy.x > self.width - 4:
                enemy.vx *= -1
                enemy.x = max(3, min(self.width - 4, enemy.x))
            enemy.fire_cd -= dt
            if enemy.fire_cd <= 0 and p is not None and enemy.y > self.top:
                dx, dy = p.x - enemy.x, p.y - enemy.y
                length = max(0.001, math.hypot(dx, dy))
                speed = 4.0 + min(0.6, self.wave * 0.025)
                self.projectiles.append(Projectile(enemy.x, enemy.y + 1,
                                                   dx / length * speed, dy / length * speed,
                                                   1, "enemy", "·", C["red"], life=5.0))
                enemy.fire_cd = self.rng.uniform(3.8, 5.6) * max(0.9, 1.0 - self.wave * 0.004)
            if enemy.y > self.bottom + 1:
                self.enemies.remove(enemy)
                if p is not None:
                    self._damage_player(1)

    def _boss_width(self) -> int:
        """Width of the large ASCII boss, adapted to the current terminal."""
        return min(65, max(40, self.width - 6))

    def _update_boss(self, dt: float) -> None:
        boss = self.boss
        if boss is None:
            return
        boss.age += dt
        boss_width = self._boss_width()
        travel = max(2.0, (self.width - boss_width) / 2 - 3)
        boss.x = self.width / 2 + math.sin(boss.age * (0.42 + boss.phase * 0.06)) * travel
        boss.y = self.top + 9.0 + math.sin(boss.age * 0.8) * 0.35
        boss.attack_cd -= dt
        boss.hit_flash = max(0.0, boss.hit_flash - dt)
        if boss.attack_cd > 0:
            return

        p = self.player
        if p is None:
            return
        dx, dy = p.x - boss.x, p.y - boss.y
        base_angle = math.atan2(dy, dx)
        if boss.phase == 1:
            count, spread, speed, delay = 3, 0.38, 4.2, 2.25
        elif boss.phase == 2:
            count, spread, speed, delay = 4, 0.58, 4.8, 1.95
        else:
            count, spread, speed, delay = 5, 0.75, 5.2, 1.70
        for i in range(count):
            offset = 0 if count == 1 else (i / (count - 1) - 0.5) * spread
            angle = base_angle + offset
            self.projectiles.append(Projectile(
                boss.x, boss.y + 3,
                math.cos(angle) * speed, math.sin(angle) * speed,
                1, "enemy", "●" if boss.phase == 3 else "o",
                C["red"] if boss.phase == 3 else C["magenta"], life=5.0,
            ))
        if boss.phase >= 2 and self.rng.random() < 0.16:
            # A short side volley adds pressure without filling the whole screen.
            for direction in (-1, 1):
                self.projectiles.append(Projectile(boss.x + direction * 10, boss.y + 3,
                                                   direction * 1.4, 5.0, 1, "enemy",
                                                   "•", C["orange"], life=4.0))
        boss.attack_cd = delay

    def _update_meteors(self, dt: float) -> None:
        for meteor in self.meteors[:]:
            meteor.x += meteor.vx * dt
            meteor.y += meteor.vy * dt
            meteor.spin += dt * 7
            if meteor.y > self.bottom + 2 or meteor.x < 0 or meteor.x >= self.width:
                self.meteors.remove(meteor)

    def _update_projectiles(self, dt: float) -> None:
        for shot in self.projectiles[:]:
            shot.life -= dt
            if shot.homing and shot.owner == "player":
                target = self._nearest_target(shot.x, shot.y)
                if target is not None:
                    desired = max(-5.5, min(5.5, (target.x - shot.x) * 2.2))
                    shot.vx += (desired - shot.vx) * min(1.0, dt * 3.0)
                    shot.vy = min(-6.0, shot.vy + 0.5 * dt)
            shot.x += shot.vx * dt
            shot.y += shot.vy * dt
            if (shot.life <= 0 or shot.x < -2 or shot.x > self.width + 2
                    or shot.y < -2 or shot.y > self.height + 2):
                if shot in self.projectiles:
                    self.projectiles.remove(shot)

    def _update_powerups(self, dt: float) -> None:
        # Bonuses are independent orbital supply drops, never enemy loot.
        self.powerup_spawn_timer -= dt
        if self.powerup_spawn_timer <= 0 and not self.powerups:
            self._spawn_powerup()
            self.powerup_spawn_timer = self.rng.uniform(20.0, 29.0)

        for item in self.powerups[:]:
            item.y += item.vy * dt
            item.x += math.sin(item.age * 1.25) * 1.05 * dt
            item.x = max(7.0, min(self.width - 8.0, item.x))
            item.age += dt
            if item.y > self.bottom + 4:
                self.powerups.remove(item)

    def _update_particles(self, dt: float) -> None:
        for particle in self.particles[:]:
            particle.x += particle.vx * dt
            particle.y += particle.vy * dt
            particle.vx *= 0.97
            particle.vy *= 0.97
            particle.life -= dt
            if particle.life <= 0:
                self.particles.remove(particle)

    # ----- collisions ------------------------------------------------------

    @staticmethod
    def _near(ax: float, ay: float, bx: float, by: float, radius: float = 0.8) -> bool:
        return abs(ax - bx) <= radius and abs(ay - by) <= radius

    def _collisions(self) -> None:
        p = self.player
        # Player projectiles against enemies and the boss.
        for shot in self.projectiles[:]:
            if shot.owner != "player":
                continue
            hit = False
            for enemy in self.enemies[:]:
                hit_radius = 3.4 if enemy.kind == "tank" else 2.6 if enemy.kind == "zigzag" else 2.1
                if self._near(shot.x, shot.y, enemy.x, enemy.y, hit_radius):
                    if shot.splash > 0:
                        blast_x, blast_y = shot.x, shot.y
                        self._explode(blast_x, blast_y, 20, C["yellow"])
                        for nearby in self.enemies[:]:
                            if math.hypot(nearby.x - blast_x, nearby.y - blast_y) <= shot.splash:
                                self._damage_enemy(nearby, shot.damage)
                                if nearby.hp <= 0 and nearby in self.enemies:
                                    self.enemies.remove(nearby)
                        if self.boss is not None and math.hypot(self.boss.x - blast_x, self.boss.y - blast_y) <= shot.splash + 2:
                            self._damage_boss(shot.damage)
                        if shot in self.projectiles:
                            self.projectiles.remove(shot)
                        hit = True
                    else:
                        self._damage_enemy(enemy, shot.damage)
                        if shot.pierce > 0:
                            shot.pierce -= 1
                        else:
                            if shot in self.projectiles:
                                self.projectiles.remove(shot)
                            hit = True
                        if enemy.hp <= 0 and enemy in self.enemies:
                            self.enemies.remove(enemy)
                    if hit:
                        break
            if hit:
                continue
            if self.boss is not None:
                half_w = self._boss_width() / 2
                if abs(shot.x - self.boss.x) <= half_w and -5.0 <= shot.y - self.boss.y <= 7.0:
                    if shot.splash > 0:
                        self._explode(shot.x, shot.y, 20, C["yellow"])
                        self._damage_boss(shot.damage)
                        for nearby in self.enemies[:]:
                            if math.hypot(nearby.x - shot.x, nearby.y - shot.y) <= shot.splash:
                                self._damage_enemy(nearby, shot.damage)
                                if nearby.hp <= 0 and nearby in self.enemies:
                                    self.enemies.remove(nearby)
                        if shot in self.projectiles:
                            self.projectiles.remove(shot)
                    else:
                        self._damage_boss(shot.damage)
                        if shot.pierce > 0:
                            shot.pierce -= 1
                        elif shot in self.projectiles:
                            self.projectiles.remove(shot)

        # Enemy projectiles, enemies, and meteors against the player.
        if p is not None:
            for shot in self.projectiles[:]:
                if shot.owner == "enemy" and self._near(shot.x, shot.y, p.x, p.y, 1.0):
                    if shot in self.projectiles:
                        self.projectiles.remove(shot)
                    self._damage_player(1)
            for enemy in self.enemies[:]:
                if self._near(enemy.x, enemy.y, p.x, p.y, 1.35):
                    self.enemies.remove(enemy)
                    self._explode(enemy.x, enemy.y, 12)
                    self._damage_player(1)
            for meteor in self.meteors[:]:
                if self._near(meteor.x, meteor.y, p.x, p.y, 1.5):
                    self.meteors.remove(meteor)
                    self._explode(meteor.x, meteor.y, 18, C["orange"])
                    self._damage_player(1)

            # Player shots can break meteors.
            for shot in self.projectiles[:]:
                if shot.owner != "player":
                    continue
                for meteor in self.meteors[:]:
                    if self._near(shot.x, shot.y, meteor.x, meteor.y, 1.1):
                        if shot.splash > 0:
                            blast_x, blast_y = shot.x, shot.y
                            self._explode(blast_x, blast_y, 20, C["yellow"])
                            for nearby_meteor in self.meteors[:]:
                                if math.hypot(nearby_meteor.x - blast_x, nearby_meteor.y - blast_y) <= shot.splash:
                                    self.meteors.remove(nearby_meteor)
                                    self.score += 40
                                    self._explode(nearby_meteor.x, nearby_meteor.y, 8, C["orange"])
                            if shot in self.projectiles:
                                self.projectiles.remove(shot)
                        else:
                            meteor.hp -= shot.damage
                            if shot in self.projectiles:
                                self.projectiles.remove(shot)
                            if meteor.hp <= 0:
                                self.meteors.remove(meteor)
                                self.score += 40
                                self._explode(meteor.x, meteor.y, 10, C["orange"])
                        break

            # Collect supply drops.
            for item in self.powerups[:]:
                if self._near(item.x, item.y, p.x, p.y, 1.4):
                    self._grant_powerup(item)
                    self.powerups.remove(item)

        # Missile splash damage.
        # (A missile removed by direct collision leaves a small timed burst.)
        # Splash is applied at the instant a missile reaches its target below.

    def _check_wave_clear(self, dt: float) -> None:
        if self.boss is not None or self.spawn_left > 0 or self.meteor_left > 0 or self.enemies or self.meteors:
            self.wave_clear_timer = 0.0
            return
        self.wave_clear_timer += dt
        if self.wave_clear_timer >= 2.0:
            self.wave += 1
            self._start_wave()

    # ----- drawing ---------------------------------------------------------

    def draw(self) -> None:
        self._resize_if_needed()
        c = self.canvas
        c.clear()
        self._draw_stars()
        if self.width < MIN_WIDTH or self.height < MIN_HEIGHT:
            c.centered(max(1, self.height // 2 - 1), "УВЕЛИЧЬ РАЗМЕР ОКНА ТЕРМИНАЛА", C["yellow"])
            c.centered(max(2, self.height // 2), f"Минимум: {MIN_WIDTH} столбцов x {MIN_HEIGHT} строк", C["white"])
        elif self.state == "menu":
            self._draw_menu()
        elif self.state in ("playing", "paused"):
            self._draw_game()
            if self.state == "paused":
                self._draw_overlay("ПАУЗА", "P или ENTER — продолжить", "M — главное меню")
        elif self.state == "gameover":
            self._draw_game()
            self._draw_overlay("КОРАБЛЬ УНИЧТОЖЕН", f"Итоговый счёт: {self.score:,}", "ENTER — заново   M — меню   Q — выход")
        elif self.state == "victory":
            self._draw_game()
            self._draw_overlay("СЕКТОР ЗАЩИЩЁН!", f"Босс побеждён | Счёт: {self.score:,}", "ENTER — ещё раз   M — меню   Q — выход")
        sys.stdout.write(c.render())
        sys.stdout.flush()

    def _draw_stars(self) -> None:
        for star in self.stars:
            self.canvas.put(int(star.x), int(star.y), star.glyph, star.color)

    def _draw_menu(self) -> None:
        c = self.canvas
        c.centered(3, "  .      *        .        +          *      .  ", C["blue"])
        c.centered(5, "╔══════════════════════════════════════════════════════╗", C["cyan"])
        c.centered(6, "║                 О Р Б И Т А Л Ь Н А Я               ║", C["white"])
        c.centered(7, "║                    О Б О Р О Н А                    ║", C["white"])
        c.centered(8, "╚══════════════════════════════════════════════════════╝", C["cyan"])
        c.centered(10, "20 СЕКТОРОВ. 4 КОМАНДИРА. ОДНА ФИНАЛЬНАЯ БИТВА.", C["yellow"])
        c.centered(11, "Управляй истребителем, переживи волны и уничтожь босса.", C["gray"])
        first = "[ НАЧАТЬ МИССИЮ ]" if self.menu_selection == 0 else "  НАЧАТЬ МИССИЮ  "
        second = "[ ВЫХОД ]" if self.menu_selection == 1 else "  ВЫХОД  "
        c.centered(14, first, C["green"] if self.menu_selection == 0 else C["white"])
        c.centered(16, second, C["red"] if self.menu_selection == 1 else C["white"])
        c.centered(19, "УПРАВЛЕНИЕ", C["cyan"])
        c.centered(20, "СТРЕЛКИ / WASD — движение   ПРОБЕЛ — стрелять (держать)", C["white"])
        c.centered(21, "1 / 2 / 3 — смена оружия   X — щит   P — пауза   Q — меню", C["white"])
        c.centered(23, f"РЕКОРД: {self.high_score:,}       ВЕРСИЯ {VERSION}", C["yellow"])
        c.centered(25, "Выбери пункт стрелками и нажми ENTER или ПРОБЕЛ.", C["gray"])
        c.centered(28, r"             /A\             ", C["cyan"])
        c.centered(29, r"            <###>            ", C["blue"])

    def _draw_game(self) -> None:
        c = self.canvas
        p = self.player
        if p is None:
            return

        # HUD — полностью на русском.
        c.text(1, 0, f"{TITLE}  //  СЕКТОР {self.wave:02d}/20", C["cyan"])
        c.text(max(1, self.width - 21), 0, f"СЧЁТ {self.score:08d}", C["yellow"])
        c.text(1, 1, "КОРПУС", C["white"])
        c.bar(8, 1, 11, p.hp, p.max_hp, C["green"] if p.hp > 2 else C["red"])
        c.text(20, 1, f"{p.hp}/{p.max_hp}", C["white"])
        c.text(27, 1, "ЩИТ", C["cyan"])
        c.bar(32, 1, 12, p.shield, 100, C["cyan"])
        c.text(47, 1, "НАГРЕВ", C["white"])
        c.bar(55, 1, min(16, self.width - 57), p.heat, 100,
              C["red"] if p.overheated or p.heat > 75 else C["orange"])
        c.text(1, 2, f"ОРУЖИЕ: {WEAPONS[p.weapon]}", C["magenta"])
        c.text(29, 2, "ПРОБЕЛ — ОГОНЬ", C["green"] if p.trigger_time > 0 else C["gray"])
        if p.overheated:
            c.text(48, 2, "ПЕРЕГРЕВ!", C["red"])
        if p.shield_on:
            c.text(max(1, self.width - 15), 2, "ЩИТ ВКЛЮЧЁН", C["cyan"])

        # Playfield guide rails.
        for y in range(self.top, self.bottom + 1):
            c.put(0, y, "│", C["darkblue"])
            c.put(self.width - 1, y, "│", C["darkblue"])

        # Meteors.
        for meteor in self.meteors:
            glyph = self.rng.choice(("@", "%", "O")) if int(meteor.spin * 4) % 3 == 0 else "*"
            c.put(round(meteor.x), round(meteor.y), glyph, C["orange"])
            c.put(round(meteor.x), round(meteor.y) - 1, ".", C["red"])

        # Противники — читаемые силуэты с крыльями, кабиной и двигателями.
        # Все спрайты моноширинные ASCII, поэтому не ломают сетку Terminal.app.
        for enemy in self.enemies:
            x, y = round(enemy.x), round(enemy.y)
            if enemy.kind == "scout":
                sprite = [
                    (r"  /^^\  ", C["darkred"]),
                    (r" /####\ ", C["red"]),
                    ("<| OO |>", C["orange"]),
                    (r" \####/ ", C["red"]),
                    (r"  /==\  ", C["darkred"]),
                ]
                bar_w = 9
            elif enemy.kind == "zigzag":
                sprite = [
                    (r"\  /^^\  /", C["magenta"]),
                    (r" \######/ ", C["pink"]),
                    ("<##|OO|##>", C["magenta"]),
                    (r" /######\ ", C["pink"]),
                    (r"   /||\   ", C["magenta"]),
                ]
                bar_w = 12
            else:
                sprite = [
                    ("  .------.  ", C["yellow"]),
                    (r" /|######|\ ", C["orange"]),
                    ("|##|[OO]|##|", C["yellow"]),
                    ("|############|", C["orange"]),
                    (r" \__||||__/ ", C["yellow"]),
                ]
                bar_w = 15
            for row_i, (line, color) in enumerate(sprite):
                c.text(x - len(line) // 2, y - 2 + row_i, line, color)
            # Cyan cockpit glow and amber engine exhaust make each ship easier to read.
            c.text(x - 1, y, "OO", C["cyan"])
            c.text(x - 1, y + 2, "||", C["orange"])
            if enemy.hp < enemy.max_hp:
                c.bar(x - bar_w // 2, y - 3, bar_w, enemy.hp, enemy.max_hp, C["green"])

        # Boss: dynamic wide sprite, roughly one third of terminal width.
        if self.boss is not None:
            self._draw_boss(self.boss)

        # Projectiles.
        for shot in self.projectiles:
            color = shot.color
            glyph = shot.glyph
            c.put(round(shot.x), round(shot.y), glyph, color)
            if shot.glyph == "║":
                c.put(round(shot.x), round(shot.y) + 1, "║", C["darkcyan"])

        # Sleek orbital supply pod: streamlined hull, side fins, bright core,
        # and an animated engine trail. The center glyph hints at the reward.
        pod_marks = {
            "health": "+", "coolant": "~", "shield": "O",
            "rapid": ">", "damage": "!", "spread": "*", "score": "$",
        }
        pod_colors = {
            "health": C["green"], "coolant": C["cyan"], "shield": C["blue"],
            "rapid": C["yellow"], "damage": C["red"], "spread": C["magenta"],
            "score": C["orange"],
        }
        for item in self.powerups:
            x, y = round(item.x), round(item.y)
            pulse = int(item.age * 6) % 2 == 0
            shell = C["white"] if pulse else C["cyan"]
            accent = pod_colors.get(item.kind, C["yellow"])
            mark = pod_marks.get(item.kind, "?")
            c.text(x - 4, y - 3, "   .-.   ", C["cyan"] if pulse else C["darkcyan"])
            c.text(x - 4, y - 2, " _/###" + "\\_ ", shell)
            c.text(x - 4, y - 1, "/|     |" + "\\", shell)
            c.text(x - 4, y, "||  " + mark + "  ||", accent)
            c.text(x - 4, y + 1, "\\" + "|_____|/", shell)
            c.text(x - 4, y + 2, r"  /|||\  ", C["orange"] if pulse else C["yellow"])
            if pulse:
                c.put(x - 6, y, ".", C["cyan"])
                c.put(x + 6, y, ".", C["cyan"])

        # Particles.
        for particle in self.particles:
            if particle.life > 0:
                c.put(round(particle.x), round(particle.y), particle.glyph, particle.color)

        # Player ship and shield field.
        px, py = round(p.x), round(p.y)
        if p.shield_on:
            c.text(px - 3, py - 1, "(   )", C["cyan"])
            c.text(px - 3, py + 1, "(   )", C["darkcyan"])
        if p.invulnerable <= 0 or int(self.time_alive * 12) % 2 == 0:
            c.text(px - 2, py - 1, " /A\\ ", C["white"])
            c.text(px - 2, py, "<###>", C["cyan"])
            c.text(px - 1, py + 1, " / ", C["blue"])

        # Short event banner.
        if self.message_timer > 0 and self.message:
            c.centered(self.top + 3, self.message[:self.width - 4], C["yellow"])

        # Controls and wave status.
        if self.spawn_left > 0:
            status = f"ПРОТИВНИКОВ В ПУТИ: {self.spawn_left}"
        elif self.boss is not None:
            status = f"БОСС: ФАЗА {self.boss.phase} / 3"
        elif self.enemies or self.meteors:
            status = "УНИЧТОЖЬ ОСТАВШИХСЯ ПРОТИВНИКОВ"
        else:
            status = "СЕКТОР ОЧИЩЕН // ГОТОВИМСЯ К НОВОЙ ВОЛНЕ"
        c.text(1, self.height - 3, status, C["yellow"])
        active = []
        if p.rapid_timer > 0:
            active.append(f"СКОРОСТРЕЛЬНОСТЬ {p.rapid_timer:0.0f}с")
        if p.damage_timer > 0:
            active.append(f"УРон x1.7 {p.damage_timer:0.0f}с")
        if p.spread_timer > 0:
            active.append(f"ТРОЙНОЙ ЗАЛП {p.spread_timer:0.0f}с")
        if active:
            c.text(1, self.height - 2, "УСИЛЕНИЯ: " + " | ".join(active), C["green"])
        controls = "ДВИЖЕНИЕ WASD/СТРЕЛКИ | ПРОБЕЛ СТРЕЛЬБА (ДЕРЖАТЬ) | ОРУЖИЕ 1-3 | ЩИТ X | ПАУЗА P | МЕНЮ Q"
        c.text(1, self.height - 1, controls[:self.width - 2], C["gray"])

    def _draw_boss(self, boss: Boss) -> None:
        """Draw the large symmetrical dreadnought inspired by the supplied art."""
        c = self.canvas
        width = self._boss_width()
        cx = int(round(boss.x))
        y0 = int(round(boss.y)) - 4
        # Wide command ship: antenna, twin side weapon pods, armored core,
        # swept wings and four glowing engine bells. Each row is centered
        # independently so the sprite remains balanced at different widths.
        raw_rows = [
            r"                              /\                              ",
            r"                   _________/  \_________                   ",
            r"              ____/=====/\____/\=====\____              ",
            r"       ______/___  ___/====\__/====\___  ___\______       ",
            r"    __/  ___\__/  [####]  \__/  [####]  \__/  ___\__    ",
            r" __/====[####]===\____/====\__/====\____/===[####]====\__ ",
            r"<=======[######]===\____  CORE  ____/===[######]=======>",
            r" \___________\_____/========\_____/___________/",
            r"       \____/       \____/  \____/       \____/",
            r"          ||            ||    ||            ||",
            r"          !!            !!    !!            !!",
        ]
        colors = [
            C["pink"], C["magenta"], C["magenta"], C["magenta"],
            C["yellow"], C["orange"], C["red"], C["magenta"],
            C["magenta"], C["cyan"], C["orange"],
        ]
        if boss.phase == 2:
            colors = [C["cyan"] if col == C["magenta"] else col for col in colors]
        elif boss.phase == 3:
            colors = [C["red"] if col in (C["magenta"], C["pink"]) else col for col in colors]
        if boss.hit_flash > 0:
            colors = [C["white"]] * len(raw_rows)

        for row_i, raw in enumerate(raw_rows):
            row = raw.strip(" ")
            if len(row) > width:
                trim = (len(row) - width) // 2
                row = row[trim:trim + width]
            x = cx - len(row) // 2
            c.text(x, y0 + row_i, row, colors[row_i])

        # A bright reactor core makes the weak point easy to identify.
        core_color = C["white"] if boss.hit_flash > 0 else (C["red"] if boss.phase >= 2 else C["yellow"])
        c.text(cx - 2, y0 + 6, "CORE", core_color)

        # The boss health display stays separate from its body and always visible.
        bar_w = min(width - 12, self.width - 18)
        bar_x = max(2, (self.width - bar_w) // 2)
        c.centered(3, f"Б О С С   С Е К Т О Р А   {self.wave}", C["red"])
        c.text(bar_x, 4, "ЗДОРОВЬЕ БОССА", C["yellow"])
        c.bar(bar_x + 16, 4, max(8, bar_w - 16), boss.hp, boss.max_hp, C["red"], C["darkred"])
        c.centered(5, f"ФАЗА {boss.phase} ИЗ 3", C["magenta"])

    def _draw_overlay(self, title: str, line1: str, line2: str) -> None:
        c = self.canvas
        box_w = min(58, self.width - 8)
        x = (self.width - box_w) // 2
        y = self.height // 2 - 3
        for row in range(7):
            c.text(x, y + row, "█" + " " * (box_w - 2) + "█", C["darkblue"])
        c.text(x, y, "╔" + "═" * (box_w - 2) + "╗", C["cyan"])
        c.text(x, y + 6, "╚" + "═" * (box_w - 2) + "╝", C["cyan"])
        c.centered(y + 1, title, C["yellow"] if title != "КОРАБЛЬ УНИЧТОЖЕН" else C["red"])
        c.centered(y + 3, line1, C["white"])
        c.centered(y + 4, line2, C["gray"])

    # ----- application loop ------------------------------------------------

    def run(self) -> None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("Орбитальная оборона требует интерактивного терминала.")
            print("Откройте Terminal и запустите: python3 orbital_defense.py")
            return
        with Terminal() as terminal:
            last = time.perf_counter()
            accumulator = 0.0
            while self.running:
                now = time.perf_counter()
                frame_dt = min(0.10, now - last)
                last = now
                self.handle_keys(terminal.read_keys())
                if self.state == "playing":
                    accumulator += frame_dt
                    # Fixed timestep makes movement and collision behavior stable.
                    while accumulator >= FRAME_TIME:
                        self.update(FRAME_TIME)
                        accumulator -= FRAME_TIME
                else:
                    # Keep background stars moving on menu/pause/end screens too.
                    self._update_stars(frame_dt)
                self.draw()
                elapsed = time.perf_counter() - now
                time.sleep(max(0.0, FRAME_TIME - elapsed))


def main() -> None:
    try:
        OrbitalDefense().run()
    except KeyboardInterrupt:
        # Terminal.__exit__ restores cursor and input mode on Ctrl+C.
        pass
    except Exception as exc:
        print(f"\nИгра остановлена из-за ошибки: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
