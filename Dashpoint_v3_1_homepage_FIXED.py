from __future__ import annotations

import ast
import locale
import operator
import threading
import webbrowser
from datetime import datetime, timedelta
from tkinter import messagebox
from typing import Callable

import customtkinter as ctk
import requests
from PIL import Image, ImageDraw

APP_NAME = "Dashpoint"
APP_VERSION = "3.0"
FEEDBACK_EMAIL = "michal-medvecky@seznam.cz"
CNB_URL = "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt"
GOLD_URL = "https://api.gold-api.com/price/XAU"
WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=49.7475&longitude=13.3776"
    "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
    "&timezone=Europe%2FPrague"
)
NAMEDAY_URL = "https://svatkyapi.cz/api/day"

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

try:
    locale.setlocale(locale.LC_ALL, "Czech_Czechia.1250")
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, "cs_CZ.UTF-8")
    except locale.Error:
        pass


class SafeCalculator(ast.NodeVisitor):
    """Bezpečně vyhodnotí pouze číselné matematické výrazy."""

    BINARY_OPERATORS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

    def evaluate(self, expression: str) -> float:
        expression = expression.strip().replace(",", ".").replace("^", "**")
        if not expression:
            raise ValueError("Zadejte matematický výraz.")
        if len(expression) > 150:
            raise ValueError("Matematický výraz je příliš dlouhý.")
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValueError("Matematický výraz není platný.") from exc
        result = self.visit(tree.body)
        if abs(result) > 10**100:
            raise ValueError("Výsledek je příliš velký.")
        return float(result)

    def visit_Constant(self, node: ast.Constant) -> float:
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("Výraz smí obsahovat pouze čísla a matematické operátory.")
        return float(node.value)

    def visit_BinOp(self, node: ast.BinOp) -> float:
        operation = self.BINARY_OPERATORS.get(type(node.op))
        if operation is None:
            raise ValueError("Tato matematická operace není podporována.")
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("Exponent je příliš velký.")
        try:
            return float(operation(left, right))
        except ZeroDivisionError as exc:
            raise ValueError("Nelze dělit nulou.") from exc

    def visit_UnaryOp(self, node: ast.UnaryOp) -> float:
        operation = self.UNARY_OPERATORS.get(type(node.op))
        if operation is None:
            raise ValueError("Tato operace není podporována.")
        return float(operation(self.visit(node.operand)))

    def generic_visit(self, node: ast.AST):
        raise ValueError("Výraz obsahuje nepovolené prvky.")


class ApiClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
        self.rates: dict[str, float] = {"CZK": 1.0}
        self.rates_time: datetime | None = None
        self.gold_price: float | None = None
        self.gold_time: datetime | None = None

    def get_cnb_rates(self, force: bool = False) -> dict[str, float]:
        if (
            not force
            and self.rates_time
            and datetime.now() - self.rates_time < timedelta(hours=6)
            and len(self.rates) > 1
        ):
            return self.rates

        response = self.session.get(CNB_URL, timeout=15)
        response.raise_for_status()
        response.encoding = "utf-8"
        rates = {"CZK": 1.0}
        for line in response.text.splitlines():
            parts = line.strip().split("|")
            if len(parts) != 5:
                continue
            _, _, amount_text, code, rate_text = parts
            if len(code) != 3 or not amount_text.isdigit():
                continue
            amount = float(amount_text)
            rate = float(rate_text.replace(",", "."))
            if amount > 0:
                rates[code] = rate / amount

        for required in ("EUR", "USD", "GBP"):
            if required not in rates:
                raise RuntimeError("ČNB vrátila neúplná kurzovní data.")
        self.rates = rates
        self.rates_time = datetime.now()
        return rates

    def convert_currency(self, amount: float, source: str, target: str) -> float:
        rates = self.get_cnb_rates()
        if source not in rates or target not in rates:
            raise RuntimeError("Požadovaný měnový kurz není dostupný.")
        czk = amount if source == "CZK" else amount * rates[source]
        return czk if target == "CZK" else czk / rates[target]

    def get_gold_usd_per_ounce(self, force: bool = False) -> float:
        if (
            not force
            and self.gold_price is not None
            and self.gold_time
            and datetime.now() - self.gold_time < timedelta(seconds=30)
        ):
            return self.gold_price
        response = self.session.get(GOLD_URL, timeout=15, headers={"Accept": "application/json"})
        response.raise_for_status()
        price = float(response.json().get("price", 0))
        if price <= 0:
            raise RuntimeError("API nevrátilo platnou cenu zlata.")
        self.gold_price = price
        self.gold_time = datetime.now()
        return price

    def get_gold_prices_czk(self, force: bool = False) -> dict[str, float]:
        usd_rate = self.get_cnb_rates(force=force)["USD"]
        ounce = self.get_gold_usd_per_ounce(force=force) * usd_rate
        gram = ounce / 31.1034768
        return {"ounce": ounce, "50g": gram * 50, "100g": gram * 100}

    def get_weather(self) -> dict[str, str]:
        response = self.session.get(WEATHER_URL, timeout=10)
        response.raise_for_status()
        current = response.json()["current"]
        weather_code = int(current.get("weather_code", -1))
        descriptions = {
            0: "Jasno", 1: "Převážně jasno", 2: "Polojasno", 3: "Zataženo",
            45: "Mlha", 48: "Mrznoucí mlha", 51: "Slabé mrholení",
            53: "Mrholení", 55: "Silné mrholení", 61: "Slabý déšť",
            63: "Déšť", 65: "Silný déšť", 71: "Slabé sněžení",
            73: "Sněžení", 75: "Silné sněžení", 80: "Přeháňky",
            81: "Dešťové přeháňky", 82: "Silné přeháňky", 95: "Bouřka",
        }
        return {
            "temperature": f"{float(current['temperature_2m']):.1f} °C".replace(".", ","),
            "description": descriptions.get(weather_code, "Aktuální počasí"),
            "humidity": f"Vlhkost: {current.get('relative_humidity_2m', '–')} %",
            "wind": f"Vítr: {float(current.get('wind_speed_10m', 0)):.1f} km/h".replace(".", ","),
        }

    def get_nameday(self) -> str:
        response = self.session.get(NAMEDAY_URL, timeout=10)
        response.raise_for_status()
        return str(response.json().get("name", "Nedostupné"))


def create_icon(kind: str, size: int = 34, color: str = "#1677FF", background: str = "#E8F1FF") -> ctk.CTkImage:
    """Vytvoří čistou ikonku přímo v programu, takže nejsou potřeba externí PNG soubory."""
    scale = 3
    px = size * scale
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    def box(coords, **kwargs):
        draw.rounded_rectangle(tuple(v * scale for v in coords), radius=7 * scale, **kwargs)
    box((0, 0, size, size), fill=background)
    w = max(2, size // 13) * scale
    c = color
    if kind == "sun":
        cx=cy=size*scale/2; r=6*scale
        draw.ellipse((cx-r,cy-r,cx+r,cy+r), fill=c)
        import math
        for i in range(8):
            a=i*math.pi/4; r1=10*scale; r2=14*scale
            draw.line((cx+math.cos(a)*r1,cy+math.sin(a)*r1,cx+math.cos(a)*r2,cy+math.sin(a)*r2), fill=c, width=w)
    elif kind == "moon":
        draw.ellipse((8*scale,6*scale,27*scale,27*scale), fill=c)
        draw.ellipse((14*scale,3*scale,30*scale,22*scale), fill=background)
    elif kind == "cloud":
        draw.ellipse((6*scale,14*scale,20*scale,27*scale), fill=c)
        draw.ellipse((13*scale,8*scale,27*scale,26*scale), fill=c)
        draw.ellipse((20*scale,15*scale,30*scale,27*scale), fill=c)
        draw.rounded_rectangle((6*scale,19*scale,30*scale,28*scale), radius=4*scale, fill=c)
    elif kind == "gift":
        draw.rectangle((8*scale,15*scale,27*scale,29*scale), fill=c)
        draw.rectangle((6*scale,11*scale,29*scale,16*scale), fill=c)
        draw.line((17.5*scale,11*scale,17.5*scale,29*scale), fill=background, width=w)
        draw.arc((8*scale,4*scale,18*scale,14*scale), 180, 360, fill=c, width=w)
        draw.arc((17*scale,4*scale,27*scale,14*scale), 180, 360, fill=c, width=w)
    elif kind == "clock":
        draw.ellipse((6*scale,6*scale,29*scale,29*scale), outline=c, width=w)
        draw.line((17.5*scale,10*scale,17.5*scale,18*scale,23*scale,22*scale), fill=c, width=w, joint="curve")
    elif kind == "calendar":
        draw.rounded_rectangle((7*scale,8*scale,28*scale,29*scale), radius=4*scale, outline=c, width=w)
        draw.rectangle((7*scale,13*scale,28*scale,18*scale), fill=c)
        draw.line((12*scale,5*scale,12*scale,11*scale), fill=c, width=w)
        draw.line((23*scale,5*scale,23*scale,11*scale), fill=c, width=w)
        draw.ellipse((12*scale,21*scale,15*scale,24*scale), fill=c)
        draw.ellipse((20*scale,21*scale,23*scale,24*scale), fill=c)
    elif kind == "info":
        draw.ellipse((7*scale,7*scale,28*scale,28*scale), outline=c, width=w)
        draw.ellipse((16*scale,11*scale,19*scale,14*scale), fill=c)
        draw.line((17.5*scale,17*scale,17.5*scale,24*scale), fill=c, width=w)
    return ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))


class ResultPanel(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, corner_radius=14, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.title = ctk.CTkLabel(self, text="Připraveno", font=ctk.CTkFont(size=16, weight="bold"))
        self.title.grid(row=0, column=0, padx=20, pady=(18, 5))
        self.value = ctk.CTkLabel(
            self, text="Vyberte výpočet a zadejte hodnotu.",
            font=ctk.CTkFont(size=26, weight="bold"), wraplength=420, justify="center"
        )
        self.value.grid(row=1, column=0, padx=20, pady=5)
        self.detail = ctk.CTkLabel(
            self, text="Měny se načítají z ČNB, zlato z veřejného API.",
            text_color=("#667085", "#98A2B3"), wraplength=420, justify="center"
        )
        self.detail.grid(row=2, column=0, padx=20, pady=(4, 18))

    def show(self, title: str, value: str, detail: str = "", color: str | tuple = ("#12A150", "#45D483")):
        self.title.configure(text=title)
        self.value.configure(text=value, text_color=color)
        self.detail.configure(text=detail)

    def error(self, message: str):
        self.show("Něco se nepovedlo", message, "Zkontrolujte zadané hodnoty nebo připojení k internetu.", ("#C62828", "#FF6B6B"))


class DashpointApp(ctk.CTk):
    BLUE = "#1677FF"
    BLUE_HOVER = "#0F65DB"
    GREEN = ("#079455", "#45D483")
    CARD = ("#FFFFFF", "#17212B")
    MUTED = ("#667085", "#98A2B3")
    BORDER = ("#E4E7EC", "#273647")

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} | v{APP_VERSION}")
        self.geometry("1180x740")
        self.minsize(1060, 680)
        self.configure(fg_color=("#F4F7FB", "#0B1118"))
        self.api = ApiClient()
        self.calculator = SafeCalculator()
        self.decimal_places = 2
        self.current_page = "Domů"
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.calc_mode = "Základní"
        self.currency_from = "CZK"
        self.currency_to = "EUR"
        self.weather_labels: dict[str, ctk.CTkLabel] = {}
        self.icons: dict[str, ctk.CTkImage] = {}

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self._build_header()
        self._build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=1, sticky="nsew", padx=(18, 24), pady=(12, 24))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.show_home()
        self.bind("<Return>", lambda _event: self._on_enter())
        self.after(1000, self._tick_clock)
        self.after(500, self._refresh_overview_async)
        self.after(300000, self._periodic_refresh)

    def _build_header(self):
        header = ctk.CTkFrame(self, height=68, corner_radius=0, fg_color=self.CARD, border_width=1, border_color=self.BORDER)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        self.icons["sun"] = create_icon("sun", 28, "#F5A524", "#FFF7E6")
        self.icons["moon"] = create_icon("moon", 28, "#2F6FEB", "#EAF1FF")

        logo = ctk.CTkLabel(header, text="◉", text_color=self.BLUE, font=ctk.CTkFont(size=28, weight="bold"), cursor="hand2")
        logo.grid(row=0, column=0, rowspan=2, padx=(24, 10), pady=11)
        logo.bind("<Button-1>", lambda _event: self.show_home())

        self.brand_title = ctk.CTkLabel(header, text=APP_NAME, font=ctk.CTkFont(size=20, weight="bold"), cursor="hand2")
        self.brand_title.grid(row=0, column=1, sticky="sw", pady=(10, 0))
        self.brand_title.bind("<Button-1>", lambda _event: self.show_home())
        self.brand_subtitle = ctk.CTkLabel(header, text="Váš každodenní pomocník", text_color=self.MUTED, font=ctk.CTkFont(size=11), cursor="hand2")
        self.brand_subtitle.grid(row=1, column=1, sticky="nw", pady=(0, 10))
        self.brand_subtitle.bind("<Button-1>", lambda _event: self.show_home())

        theme_box = ctk.CTkFrame(header, fg_color=("#F7F9FC", "#101A24"), corner_radius=18, border_width=1, border_color=self.BORDER)
        theme_box.grid(row=0, column=2, rowspan=2, padx=(10, 24), pady=12)
        ctk.CTkLabel(theme_box, text="", image=self.icons["sun"]).grid(row=0, column=0, padx=(7, 2), pady=3)
        self.theme_switch = ctk.CTkSwitch(theme_box, text="", width=46, command=self.toggle_theme, onvalue=1, offvalue=0)
        self.theme_switch.grid(row=0, column=1, padx=2)
        ctk.CTkLabel(theme_box, text="", image=self.icons["moon"]).grid(row=0, column=2, padx=(2, 7), pady=3)
        if ctk.get_appearance_mode() == "Dark":
            self.theme_switch.select()

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=202, corner_radius=0, fg_color=self.CARD, border_width=1, border_color=self.BORDER)
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(9, weight=1)
        items = [
            ("⌂", "Domů", self.show_home), ("▣", "Výpočty", self.show_calculations),
            ("⇄", "Převod měn", self.show_currency), ("◆", "Cena zlata", self.show_gold),
            ("☁", "Počasí", self.show_weather), ("▦", "Svátky", self.show_nameday),
            ("◷", "Datum a čas", self.show_clock), ("⚙", "Nastavení", self.show_settings),
            ("ⓘ", "O aplikaci", self.show_about),
        ]
        for row, (icon, name, command) in enumerate(items):
            button = ctk.CTkButton(
                sidebar, text=f"{icon}    {name}", command=command, anchor="w",
                height=42, corner_radius=9, fg_color="transparent",
                hover_color=("#E8F1FF", "#15365F"), text_color=("#24324A", "#E8EEF7"),
                font=ctk.CTkFont(size=13, weight="normal")
            )
            button.grid(row=row, column=0, sticky="ew", padx=14, pady=(12 if row == 0 else 2, 2))
            self.nav_buttons[name] = button
        ctk.CTkLabel(sidebar, text=f"Dashpoint v{APP_VERSION}\nVytvořeno v Plzni", justify="left", text_color=self.MUTED, font=ctk.CTkFont(size=11)).grid(row=10, column=0, sticky="sw", padx=22, pady=22)

    def _set_active(self, name: str):
        self.current_page = name
        for item, button in self.nav_buttons.items():
            active = item == name
            button.configure(
                fg_color=("#DCEBFF", "#104A91") if active else "transparent",
                text_color=("#075CC9", "#FFFFFF") if active else ("#24324A", "#E8EEF7"),
                font=ctk.CTkFont(size=13, weight="bold" if active else "normal"),
            )

    def _clear_content(self):
        for child in self.content.winfo_children():
            child.destroy()

    def _page(self, title: str, subtitle: str, active: str) -> ctk.CTkFrame:
        self._clear_content()
        self._set_active(active)
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(page, text=title, font=ctk.CTkFont(size=27, weight="bold")).grid(row=0, column=0, sticky="w", padx=4)
        ctk.CTkLabel(page, text=subtitle, text_color=self.MUTED, font=ctk.CTkFont(size=13)).grid(row=1, column=0, sticky="w", padx=4, pady=(2, 16))
        return page

    def _card(self, master, **grid) -> ctk.CTkFrame:
        card = ctk.CTkFrame(master, fg_color=self.CARD, corner_radius=14, border_width=1, border_color=self.BORDER)
        card.grid(**grid)
        return card

    def show_home(self):
        page = self._page("Dobrý den!", "Vše důležité na jednom místě.", "Domů")
        page.grid_columnconfigure((0, 1), weight=1, uniform="home")
        page.grid_rowconfigure((2, 3), weight=1)

        self.icons["weather"] = create_icon("cloud", 42, "#1677FF", "#E9F2FF")
        self.icons["nameday"] = create_icon("gift", 42, "#F04458", "#FFF0F1")
        self.icons["clock"] = create_icon("clock", 42, "#1677FF", "#E9F2FF")
        self.icons["overview"] = create_icon("calendar", 42, "#7A5AF8", "#F2EFFF")

        weather = self._card(page, row=2, column=0, sticky="nsew", padx=(4, 7), pady=(0, 7))
        nameday = self._card(page, row=2, column=1, sticky="nsew", padx=(7, 4), pady=(0, 7))
        clock = self._card(page, row=3, column=0, sticky="nsew", padx=(4, 7), pady=(7, 4))
        overview = self._card(page, row=3, column=1, sticky="nsew", padx=(7, 4), pady=(7, 4))
        for card in (weather, nameday, clock, overview):
            card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(weather, text="", image=self.icons["weather"]).grid(row=0, column=0, rowspan=3, sticky="n", padx=(18, 10), pady=18)
        ctk.CTkLabel(weather, text="Počasí – Plzeň", font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=1, sticky="w", pady=(18, 5))
        self.home_weather = ctk.CTkLabel(weather, text="Načítám…", font=ctk.CTkFont(size=25, weight="bold"))
        self.home_weather.grid(row=1, column=1, sticky="w")
        self.home_weather_detail = ctk.CTkLabel(weather, text="", text_color=self.MUTED, justify="left")
        self.home_weather_detail.grid(row=2, column=1, sticky="w", pady=(3, 18), padx=(0, 12))

        ctk.CTkLabel(nameday, text="", image=self.icons["nameday"]).grid(row=0, column=0, rowspan=3, sticky="n", padx=(18, 10), pady=18)
        ctk.CTkLabel(nameday, text="Dnešní svátek", font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=1, sticky="w", pady=(18, 5))
        self.home_nameday = ctk.CTkLabel(nameday, text="Načítám…", font=ctk.CTkFont(size=25, weight="bold"))
        self.home_nameday.grid(row=1, column=1, sticky="w")
        ctk.CTkLabel(nameday, text="Všechno nejlepší!", text_color=self.MUTED).grid(row=2, column=1, sticky="w", pady=(3, 18))

        ctk.CTkLabel(clock, text="", image=self.icons["clock"]).grid(row=0, column=0, rowspan=3, sticky="n", padx=(18, 10), pady=18)
        ctk.CTkLabel(clock, text="Aktuální čas", font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=1, sticky="w", pady=(18, 5))
        self.home_time = ctk.CTkLabel(clock, text="", font=ctk.CTkFont(size=28, weight="bold"))
        self.home_time.grid(row=1, column=1, sticky="w")
        ctk.CTkLabel(clock, text="Místní čas – Praha", text_color=self.MUTED).grid(row=2, column=1, sticky="w", pady=(3, 18))

        ctk.CTkLabel(overview, text="", image=self.icons["overview"]).grid(row=0, column=0, rowspan=5, sticky="n", padx=(18, 10), pady=18)
        ctk.CTkLabel(overview, text="Dnešní informace", font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=1, sticky="w", pady=(18, 6))
        self.home_day = ctk.CTkLabel(overview, text="", font=ctk.CTkFont(size=17, weight="bold"), justify="left")
        self.home_day.grid(row=1, column=1, sticky="w")
        self.home_date = ctk.CTkLabel(overview, text="", text_color=self.MUTED, justify="left")
        self.home_date.grid(row=2, column=1, sticky="w", pady=(1, 3))
        self.home_summary = ctk.CTkLabel(overview, text="Svátek: načítám…\nPočasí: načítám…", text_color=self.MUTED, justify="left")
        self.home_summary.grid(row=3, column=1, sticky="w", pady=(0, 18), padx=(0, 12))

        self._update_clock_labels()
        self._refresh_overview_async()

    def show_calculations(self):
        page = self._page("Výpočty", "Rychlé a přesné výpočty pro každý den.", "Výpočty")
        tabs = ctk.CTkSegmentedButton(page, values=["Základní", "DPH", "Procenta"], command=self._change_calc_mode, selected_color=self.BLUE, selected_hover_color=self.BLUE_HOVER)
        tabs.grid(row=2, column=0, sticky="w", padx=4, pady=(0, 14))
        tabs.set(self.calc_mode if self.calc_mode in ("Základní", "DPH", "Procenta") else "Základní")
        self.calc_container = ctk.CTkFrame(page, fg_color="transparent")
        self.calc_container.grid(row=3, column=0, sticky="nsew")
        self.calc_container.grid_columnconfigure((0, 1), weight=1)
        self._render_calc_form(tabs.get())

    def _change_calc_mode(self, value: str):
        self.calc_mode = value
        self._render_calc_form(value)

    def _render_calc_form(self, mode: str):
        for child in self.calc_container.winfo_children():
            child.destroy()
        form = self._card(self.calc_container, row=0, column=0, sticky="nsew", padx=(0, 7))
        form.grid_columnconfigure(0, weight=1)
        self.calc_result = ResultPanel(self.calc_container, fg_color=self.CARD, border_width=1, border_color=self.BORDER)
        self.calc_result.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self.calc_entries = []

        if mode == "Základní":
            labels = [("Matematický výraz", "např. (1500 + 250) * 1.21")]
        elif mode == "DPH":
            labels = [("Částka", "např. 1000")]
            self.dph_action = ctk.CTkSegmentedButton(form, values=["Připočítat DPH", "Odečíst DPH"], selected_color=self.BLUE)
            self.dph_action.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 8))
            self.dph_action.set("Připočítat DPH")
        else:
            labels = [("Hodnota", "např. 150"), ("Celek", "např. 1200")]
            self.percent_action = ctk.CTkSegmentedButton(form, values=["Kolik je to procent", "Vypočítat podíl"], selected_color=self.BLUE)
            self.percent_action.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 8))
            self.percent_action.set("Kolik je to procent")

        start = 0 if mode == "Základní" else 1
        for index, (label, placeholder) in enumerate(labels, start=start):
            ctk.CTkLabel(form, text=label, font=ctk.CTkFont(weight="bold")).grid(row=index * 2 + 1, column=0, sticky="w", padx=20, pady=(12, 5))
            entry = ctk.CTkEntry(form, placeholder_text=placeholder, height=43, corner_radius=9)
            entry.grid(row=index * 2 + 2, column=0, sticky="ew", padx=20)
            self.calc_entries.append(entry)
        button_row = start * 2 + len(labels) * 2 + 2
        ctk.CTkButton(form, text="VYPOČÍTAT", command=self.calculate, height=43, fg_color=self.BLUE, hover_color=self.BLUE_HOVER, font=ctk.CTkFont(weight="bold")).grid(row=button_row, column=0, sticky="ew", padx=20, pady=20)
        self.calc_entries[0].focus()

    def calculate(self):
        try:
            if self.calc_mode == "Základní":
                result = self.calculator.evaluate(self.calc_entries[0].get())
                self.calc_result.show("Výsledek", self.format_number(result), "Výpočet byl úspěšně dokončen.")
            elif self.calc_mode == "DPH":
                value = self.parse_number(self.calc_entries[0].get())
                if self.dph_action.get() == "Připočítat DPH":
                    tax, total = value * 0.21, value * 1.21
                    self.calc_result.show("DPH", f"{self.format_money(total)}", f"Bez DPH: {self.format_money(value)}\nDPH 21 %: {self.format_money(tax)}")
                else:
                    base = value / 1.21
                    self.calc_result.show("Bez DPH", self.format_money(base), f"S DPH: {self.format_money(value)}\nDPH 21 %: {self.format_money(value - base)}")
            else:
                first, whole = map(lambda item: self.parse_number(item.get()), self.calc_entries)
                if self.percent_action.get() == "Kolik je to procent":
                    if whole == 0:
                        raise ValueError("Nelze dělit nulou.")
                    result = first / whole * 100
                    self.calc_result.show("Výsledek", f"{self.format_number(result)} %", f"{self.format_number(first)} z {self.format_number(whole)}")
                else:
                    result = first / 100 * whole
                    self.calc_result.show("Procentní podíl", self.format_number(result), f"{self.format_number(first)} % z {self.format_number(whole)}")
        except Exception as exc:
            self.calc_result.error(str(exc))

    def show_currency(self):
        page = self._page("Převod měn", "Aktuální kurzy ČNB, přehledně a bez zbytečností.", "Převod měn")
        card = self._card(page, row=2, column=0, sticky="ew", padx=4)
        card.grid_columnconfigure((0, 2), weight=1)
        ctk.CTkLabel(card, text="Z konvertovat", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w", padx=20, pady=(20, 6))
        ctk.CTkLabel(card, text="Na měnu", font=ctk.CTkFont(weight="bold")).grid(row=0, column=2, sticky="w", padx=20, pady=(20, 6))
        self.currency_amount = ctk.CTkEntry(card, height=43, placeholder_text="Částka")
        self.currency_amount.grid(row=1, column=0, sticky="ew", padx=20)
        self.currency_from_menu = ctk.CTkOptionMenu(card, values=["CZK", "EUR", "USD", "GBP"], height=43, command=lambda value: setattr(self, "currency_from", value))
        self.currency_from_menu.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 20)); self.currency_from_menu.set(self.currency_from)
        ctk.CTkButton(card, text="⇄", width=50, height=43, command=self.swap_currencies, fg_color=("#E8F1FF", "#15365F"), text_color=("#075CC9", "#FFFFFF")).grid(row=1, column=1, rowspan=2, padx=4)
        self.currency_to_menu = ctk.CTkOptionMenu(card, values=["CZK", "EUR", "USD", "GBP"], height=43, command=lambda value: setattr(self, "currency_to", value))
        self.currency_to_menu.grid(row=1, column=2, sticky="ew", padx=20); self.currency_to_menu.set(self.currency_to)
        ctk.CTkButton(card, text="PŘEVÉST", command=self.convert_currency, height=43, fg_color=self.BLUE, hover_color=self.BLUE_HOVER).grid(row=2, column=2, sticky="ew", padx=20, pady=(8, 20))
        self.currency_result = ResultPanel(page, fg_color=self.CARD, border_width=1, border_color=self.BORDER)
        self.currency_result.grid(row=3, column=0, sticky="ew", padx=4, pady=14)
        self.currency_amount.focus()

    def swap_currencies(self):
        source, target = self.currency_from_menu.get(), self.currency_to_menu.get()
        self.currency_from_menu.set(target); self.currency_to_menu.set(source)
        self.currency_from, self.currency_to = target, source

    def convert_currency(self):
        try:
            amount = self.parse_number(self.currency_amount.get())
            source, target = self.currency_from_menu.get(), self.currency_to_menu.get()
            self.currency_result.show("Načítám kurz…", "", "")
            self.run_async(lambda: self.api.convert_currency(amount, source, target), lambda result: self.currency_result.show("Výsledek", f"{self.format_number(result)} {target}", f"{self.format_number(amount)} {source} podle aktuálního kurzu ČNB."), self.currency_result.error)
        except Exception as exc:
            self.currency_result.error(str(exc))

    def show_gold(self):
        page = self._page("Cena zlata", "Aktuální orientační spotová cena zlata.", "Cena zlata")
        self.gold_status = ctk.CTkLabel(page, text="Načítám aktuální cenu…", text_color=self.MUTED)
        self.gold_status.grid(row=2, column=0, sticky="w", padx=4)
        self.gold_cards = ctk.CTkFrame(page, fg_color="transparent")
        self.gold_cards.grid(row=3, column=0, sticky="ew", pady=12)
        self.gold_cards.grid_columnconfigure((0, 1, 2), weight=1)
        self.gold_value_labels = {}
        for column, (key, title) in enumerate((("ounce", "1 trojská unce"), ("50g", "50 gramů"), ("100g", "100 gramů"))):
            card = self._card(self.gold_cards, row=0, column=column, sticky="nsew", padx=5)
            ctk.CTkLabel(card, text="◆", text_color="#D6A514", font=ctk.CTkFont(size=28)).pack(pady=(22, 8))
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=14, weight="bold")).pack()
            label = ctk.CTkLabel(card, text="…", text_color=self.GREEN, font=ctk.CTkFont(size=24, weight="bold"))
            label.pack(pady=(10, 22)); self.gold_value_labels[key] = label
        info = self._card(page, row=4, column=0, sticky="ew", padx=4)
        ctk.CTkLabel(info, text="ⓘ  Spotová cena bez obchodní marže, dopravy a dalších poplatků.", text_color=self.MUTED).pack(anchor="w", padx=20, pady=18)
        ctk.CTkButton(page, text="Obnovit cenu", command=lambda: self._load_gold(True), width=150, fg_color=self.BLUE).grid(row=5, column=0, sticky="e", padx=4, pady=14)
        self._load_gold(False)

    def _load_gold(self, force: bool):
        if not hasattr(self, "gold_status"):
            return
        self.gold_status.configure(text="Načítám aktuální cenu…")
        self.run_async(lambda: self.api.get_gold_prices_czk(force), self._display_gold, lambda error: self.gold_status.configure(text=f"Chyba: {error}"))

    def _display_gold(self, prices: dict[str, float]):
        if self.current_page != "Cena zlata":
            return
        for key, value in prices.items():
            self.gold_value_labels[key].configure(text=self.format_money(value))
        self.gold_status.configure(text=f"Aktualizováno: {datetime.now():%d.%m.%Y %H:%M}")

    def show_weather(self):
        page = self._page("Počasí v Plzni", "Aktuální údaje z Open-Meteo.", "Počasí")
        card = self._card(page, row=2, column=0, sticky="ew", padx=4)
        card.grid_columnconfigure(0, weight=1)
        self.weather_main = ctk.CTkLabel(card, text="Načítám…", font=ctk.CTkFont(size=46, weight="bold"))
        self.weather_main.grid(row=0, column=0, padx=24, pady=(28, 5))
        self.weather_description = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=18, weight="bold"))
        self.weather_description.grid(row=1, column=0)
        self.weather_details = ctk.CTkLabel(card, text="", text_color=self.MUTED, font=ctk.CTkFont(size=14))
        self.weather_details.grid(row=2, column=0, padx=24, pady=(8, 28))
        ctk.CTkButton(page, text="Obnovit počasí", command=self._refresh_weather_page, width=160, fg_color=self.BLUE).grid(row=3, column=0, sticky="e", padx=4, pady=14)
        self._refresh_weather_page()

    def _refresh_weather_page(self):
        self.run_async(self.api.get_weather, self._display_weather_page, lambda error: self.weather_main.configure(text=f"Chyba: {error}"))

    def _display_weather_page(self, data: dict[str, str]):
        if self.current_page != "Počasí":
            return
        self.weather_main.configure(text=data["temperature"])
        self.weather_description.configure(text=data["description"])
        self.weather_details.configure(text=f"{data['humidity']}    •    {data['wind']}\nAktualizováno {datetime.now():%H:%M}")

    def show_nameday(self):
        page = self._page("Svátky", "Kdo má dnes svátek?", "Svátky")
        card = self._card(page, row=2, column=0, sticky="ew", padx=4)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="🎁", font=ctk.CTkFont(size=46)).grid(row=0, column=0, pady=(28, 4))
        self.nameday_large = ctk.CTkLabel(card, text="Načítám…", font=ctk.CTkFont(size=36, weight="bold"))
        self.nameday_large.grid(row=1, column=0)
        ctk.CTkLabel(card, text=datetime.now().strftime("%d. %m. %Y"), text_color=self.MUTED).grid(row=2, column=0, pady=(5, 28))
        self.run_async(self.api.get_nameday, lambda name: self.nameday_large.configure(text=name), lambda _error: self.nameday_large.configure(text="Nedostupné"))

    def show_clock(self):
        page = self._page("Datum a čas", "Přesný místní čas.", "Datum a čas")
        card = self._card(page, row=2, column=0, sticky="ew", padx=4)
        card.grid_columnconfigure(0, weight=1)
        self.clock_large = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=54, weight="bold"))
        self.clock_large.grid(row=0, column=0, pady=(35, 4))
        self.date_large = ctk.CTkLabel(card, text="", text_color=self.MUTED, font=ctk.CTkFont(size=20))
        self.date_large.grid(row=1, column=0, pady=(0, 35))
        self._update_clock_labels()

    def show_settings(self):
        page = self._page("Nastavení", "Přizpůsobte si chování aplikace.", "Nastavení")
        card = self._card(page, row=2, column=0, sticky="ew", padx=4)
        card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(card, text="Počet desetinných míst", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w", padx=20, pady=20)
        self.decimal_option = ctk.CTkOptionMenu(card, values=[str(number) for number in range(0, 11)], command=lambda value: setattr(self, "decimal_places", int(value)))
        self.decimal_option.grid(row=0, column=1, sticky="e", padx=20); self.decimal_option.set(str(self.decimal_places))
        ctk.CTkLabel(card, text="Motiv aplikace", font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 20))
        theme = ctk.CTkSegmentedButton(card, values=["Světlý", "Tmavý", "Systémový"], command=self._set_theme_from_text, selected_color=self.BLUE)
        theme.grid(row=1, column=1, sticky="e", padx=20, pady=(0, 20)); theme.set({"Light": "Světlý", "Dark": "Tmavý"}.get(ctk.get_appearance_mode(), "Systémový"))

    def show_about(self):
        page = self._page("O aplikaci", "Informace o programu Dashpoint.", "O aplikaci")
        card = self._card(page, row=2, column=0, sticky="ew", padx=4)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="◉", text_color=self.BLUE, font=ctk.CTkFont(size=48, weight="bold")).grid(row=0, column=0, pady=(28, 4))
        ctk.CTkLabel(card, text=f"Dashpoint v{APP_VERSION}", font=ctk.CTkFont(size=25, weight="bold")).grid(row=1, column=0)
        ctk.CTkLabel(card, text="Moderní pomocník pro výpočty, kurzy měn, cenu zlata, počasí a svátky.", text_color=self.MUTED, wraplength=600).grid(row=2, column=0, padx=30, pady=10)
        ctk.CTkButton(card, text="Poslat zpětnou vazbu", command=self.open_feedback, fg_color=self.BLUE, hover_color=self.BLUE_HOVER).grid(row=3, column=0, pady=(10, 28))

    def open_feedback(self):
        subject = f"Zpětná vazba k programu Dashpoint v{APP_VERSION}"
        webbrowser.open(f"mailto:{FEEDBACK_EMAIL}?subject={subject.replace(' ', '%20')}")

    def _on_enter(self):
        if self.current_page == "Výpočty":
            self.calculate()
        elif self.current_page == "Převod měn":
            self.convert_currency()

    def toggle_theme(self):
        ctk.set_appearance_mode("dark" if self.theme_switch.get() else "light")

    def _set_theme_from_text(self, value: str):
        mapping = {"Světlý": "light", "Tmavý": "dark", "Systémový": "system"}
        ctk.set_appearance_mode(mapping[value])
        if value == "Tmavý": self.theme_switch.select()
        elif value == "Světlý": self.theme_switch.deselect()

    def parse_number(self, text: str) -> float:
        cleaned = text.strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
        if not cleaned:
            raise ValueError("Zadejte číslo.")
        try:
            return float(cleaned)
        except ValueError as exc:
            raise ValueError("Zadejte platné číslo.") from exc

    def format_number(self, value: float) -> str:
        text = f"{value:,.{self.decimal_places}f}"
        return text.replace(",", "X").replace(".", ",").replace("X", " ")

    def format_money(self, value: float) -> str:
        return f"{self.format_number(value)} Kč"

    def run_async(self, work: Callable, success: Callable, failure: Callable):
        def runner():
            try:
                result = work()
            except Exception as exc:
                self.after(0, lambda error=str(exc): failure(error))
            else:
                self.after(0, lambda: success(result))
        threading.Thread(target=runner, daemon=True).start()

    def _refresh_overview_async(self):
        self.run_async(self.api.get_weather, self._set_home_weather, lambda _error: self._set_home_weather(None))
        self.run_async(self.api.get_nameday, self._set_home_nameday, lambda _error: self._set_home_nameday("Nedostupné"))

    def _set_home_weather(self, data: dict[str, str] | None):
        if self.current_page != "Domů" or not hasattr(self, "home_weather"):
            return
        if data:
            self.home_weather.configure(text=data["temperature"])
            self.home_weather_detail.configure(text=f"{data['description']}\n{data['humidity']}  •  {data['wind']}")
            if hasattr(self, "home_summary"):
                nameday = self.home_nameday.cget("text") if hasattr(self, "home_nameday") else "načítám…"
                self.home_summary.configure(text=f"Svátek: {nameday}\nPočasí: {data['description']}, {data['temperature']}")
        else:
            self.home_weather.configure(text="Nedostupné")
            self.home_weather_detail.configure(text="Zkontrolujte připojení k internetu.")

    def _set_home_nameday(self, name: str):
        if self.current_page == "Domů" and hasattr(self, "home_nameday"):
            self.home_nameday.configure(text=name)
            if hasattr(self, "home_summary"):
                weather_text = self.home_weather.cget("text") if hasattr(self, "home_weather") else "načítám…"
                self.home_summary.configure(text=f"Svátek: {name}\nPočasí: {weather_text}")

    def _periodic_refresh(self):
        if self.current_page == "Domů":
            self._refresh_overview_async()
        elif self.current_page == "Počasí":
            self._refresh_weather_page()
        self.after(300000, self._periodic_refresh)

    def _tick_clock(self):
        self._update_clock_labels()
        self.after(1000, self._tick_clock)

    def _update_clock_labels(self):
        now = datetime.now()
        days = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
        months = ["ledna", "února", "března", "dubna", "května", "června", "července", "srpna", "září", "října", "listopadu", "prosince"]
        date_text = f"{days[now.weekday()]}, {now.day}. {months[now.month - 1]} {now.year}"
        if hasattr(self, "home_time") and self.current_page == "Domů":
            self.home_time.configure(text=now.strftime("%H:%M:%S"))
            self.home_day.configure(text=days[now.weekday()].capitalize())
            self.home_date.configure(text=f"{now.day}. {months[now.month - 1]} {now.year}")
        if hasattr(self, "clock_large") and self.current_page == "Datum a čas":
            self.clock_large.configure(text=now.strftime("%H:%M:%S"))
            self.date_large.configure(text=date_text.capitalize())


if __name__ == "__main__":
    app = DashpointApp()
    app.mainloop()
