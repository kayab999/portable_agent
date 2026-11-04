#!/usr/bin/env python3
"""
knowledge_db.py — Structured Knowledge Base for Bonsai Portable
Zero external dependencies. Pure SQLite with curated tables.

Tables:
  - formulas: mathematical, physics, engineering formulas
  - history: dates, events, figures
  - constants: scientific constants
  - authors: philosophers, writers, their works and dates
  - concepts: key definitions and explanations

Stores in ./data/knowledge.db
"""

import sqlite3
from pathlib import Path

# ─── PATHS ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
DB_DIR = ROOT / "data"
DB_PATH = DB_DIR / "knowledge.db"


class KnowledgeDB:
    """Structured knowledge base — exact data, zero hallucination."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = None
        self._init_db()
        self._seed_if_empty()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _init_db(self):
        c = self._connect()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS formulas (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                category   TEXT NOT NULL,
                name       TEXT NOT NULL,
                formula    TEXT NOT NULL,
                variables  TEXT,
                notes      TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_formulas
                USING fts5(category, name, formula, variables, notes);

            CREATE TABLE IF NOT EXISTS history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                year       TEXT NOT NULL,
                event      TEXT NOT NULL,
                figures    TEXT,
                context    TEXT,
                tags       TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_history
                USING fts5(year, event, figures, context, tags);

            CREATE TABLE IF NOT EXISTS constants (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                symbol     TEXT,
                value      TEXT NOT NULL,
                unit       TEXT,
                field      TEXT,
                notes      TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_constants
                USING fts5(name, symbol, value, unit, field, notes);

            CREATE TABLE IF NOT EXISTS authors (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                birth      TEXT,
                death      TEXT,
                movement   TEXT,
                key_works  TEXT,
                concepts   TEXT,
                notes      TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_authors
                USING fts5(name, birth, death, movement, key_works, concepts, notes);

            CREATE TABLE IF NOT EXISTS concepts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                term       TEXT NOT NULL,
                field      TEXT NOT NULL,
                definition TEXT NOT NULL,
                related    TEXT,
                notes      TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_concepts
                USING fts5(term, field, definition, related, notes);
        """)
        c.commit()

    # ─── SEED DATA ────────────────────────────────────────────────────────

    def _seed_if_empty(self):
        c = self._connect()
        count = c.execute("SELECT COUNT(*) FROM formulas").fetchone()[0]
        if count == 0:
            self._seed_all()

    def _seed_all(self):
        c = self._connect()

        # ── FORMULAS ─────────────────────────────────────────────────────
        formulas = [
            # Math
            (
                "matematicas",
                "Teorema de Pitágoras",
                "a² = b² + c²",
                "a=hipotenusa, b=cateto1, c=cateto2",
                "Triángulo rectángulo",
            ),
            (
                "matematicas",
                "Área del círculo",
                "A = π · r²",
                "A=área, r=radio, π≈3.14159",
                "",
            ),
            (
                "matematicas",
                "Volumen de la esfera",
                "V = (4/3) · π · r³",
                "V=volumen, r=radio",
                "",
            ),
            (
                "matematicas",
                "Ecuación cuadrática",
                "x = (-b ± √(b²-4ac)) / 2a",
                "a,b,c=coeficientes",
                "Fórmula general para ax²+bx+c=0",
            ),
            (
                "matematicas",
                "Derivada de xⁿ",
                "d/dx(xⁿ) = n·xⁿ⁻¹",
                "n=exponente",
                "Regla de potencia",
            ),
            (
                "matematicas",
                "Integral de xⁿ",
                "∫xⁿ dx = xⁿ⁺¹/(n+1) + C",
                "n≠-1, C=constante",
                "Regla de integración de potencia",
            ),
            (
                "matematicas",
                "Identidad de Euler",
                "e^(iπ) + 1 = 0",
                "e=número de Euler, i=unidad imaginaria, π=pi",
                "Relación entre las 5 constantes fundamentales",
            ),
            (
                "matematicas",
                "Teorema fundamental del cálculo",
                "∫ₐᵇ f(x)dx = F(b) - F(a)",
                "F=antiderivada de f",
                "Conecta derivación e integración",
            ),
            # Physics
            (
                "fisica",
                "Equivalencia masa-energía",
                "E = m · c²",
                "E=energía, m=masa, c=velocidad de la luz",
                "Einstein, relatividad especial",
            ),
            (
                "fisica",
                "Segunda ley de Newton",
                "F = m · a",
                "F=fuerza, m=masa, a=aceleración",
                "",
            ),
            (
                "fisica",
                "Ley de gravitación universal",
                "F = G · (m₁·m₂) / r²",
                "G=6.674×10⁻¹¹ N·m²/kg², m=masas, r=distancia",
                "Newton",
            ),
            (
                "fisica",
                "Energía cinética",
                "Ec = ½ · m · v²",
                "m=masa, v=velocidad",
                "",
            ),
            (
                "fisica",
                "Energía potencial gravitatoria",
                "Ep = m · g · h",
                "m=masa, g=9.81 m/s², h=altura",
                "",
            ),
            (
                "fisica",
                "Ley de Ohm",
                "V = I · R",
                "V=voltaje, I=corriente, R=resistencia",
                "",
            ),
            (
                "fisica",
                "Ecuación de onda",
                "v = λ · f",
                "v=velocidad, λ=longitud de onda, f=frecuencia",
                "",
            ),
            (
                "fisica",
                "Entropía (Boltzmann)",
                "S = k_B · ln(Ω)",
                "k_B=constante de Boltzmann, Ω=microestados",
                "",
            ),
            (
                "fisica",
                "Primera ley termodinámica",
                "ΔU = Q - W",
                "ΔU=cambio energía interna, Q=calor, W=trabajo",
                "",
            ),
            # Chemistry
            (
                "quimica",
                "Ecuación de gases ideales",
                "PV = nRT",
                "P=presión, V=volumen, n=moles, R=8.314 J/(mol·K), T=temperatura",
                "",
            ),
            (
                "quimica",
                "pH",
                "pH = -log₁₀[H⁺]",
                "[H⁺]=concentración de iones hidrógeno",
                "",
            ),
            (
                "quimica",
                "Ley de Beer-Lambert",
                "A = ε · c · l",
                "A=absorbancia, ε=coeficiente molar, c=concentración, l=longitud de camino",
                "",
            ),
        ]

        c.executemany(
            "INSERT INTO formulas (category, name, formula, variables, notes) VALUES (?, ?, ?, ?, ?)",
            formulas,
        )

        # ── HISTORY ──────────────────────────────────────────────────────
        history = [
            (
                "1789",
                "Revolución Francesa",
                "Robespierre, Luis XVI, Napoleón",
                "Caída del Antiguo Régimen, Declaración de los Derechos del Hombre",
                "revolución, francia, derechos",
            ),
            (
                "1776",
                "Declaración de Independencia de EE.UU.",
                "Thomas Jefferson, George Washington",
                "Las 13 colonias se independizan de Gran Bretaña",
                "independencia, estados unidos",
            ),
            (
                "1914-1918",
                "Primera Guerra Mundial",
                "Wilson, Lenin, Clemenceau",
                "Conflicto global entre las potencias centrales y los Aliados",
                "guerra, mundial",
            ),
            (
                "1939-1945",
                "Segunda Guerra Mundial",
                "Churchill, Roosevelt, Stalin, Hitler",
                "El conflicto más destructivo de la historia. Holocausto. Bomba atómica.",
                "guerra, mundial, holocausto",
            ),
            (
                "1989",
                "Caída del Muro de Berlín",
                "Gorbachov, Reagan",
                "Fin de la Guerra Fría, reunificación alemana",
                "muro, berlin, guerra fria",
            ),
            (
                "1492",
                "Descubrimiento de América",
                "Cristóbal Colón",
                "Llegada de Colón a las Américas, inicio del contacto sostenido entre ambos mundos",
                "colón, américa",
            ),
            (
                "1517",
                "Reforma Protestante",
                "Martín Lutero",
                "Lutero clava las 95 tesis en Wittenberg, cisma de la Iglesia Católica",
                "lutero, reforma, protestante",
            ),
            (
                "1781",
                "Publicación de Crítica de la razón pura",
                "Immanuel Kant",
                "Kant publica su obra magna de filosofía crítica en Königsberg",
                "kant, filosofia, critica",
            ),
            (
                "1927",
                "Publicación de Ser y Tiempo",
                "Martin Heidegger",
                "Heidegger publica Sein und Zeit, obra fundamental de la ontología del siglo XX",
                "heidegger, filosofia, ser",
            ),
            (
                "1883",
                "Publicación de El Capital Vol. I",
                "Karl Marx",
                "Marx publica su análisis del modo de producción capitalista",
                "marx, capital, economia",
            ),
            (
                "1886",
                "Más allá del bien y del mal",
                "Friedrich Nietzsche",
                "Nietzsche publica Jenseits von Gut und Böse, crítica a la moral tradicional",
                "nietzsche, filosofia, moral",
            ),
            (
                "1943",
                "El ser y la nada",
                "Jean-Paul Sartre",
                "Sartre publica L'Être et le Néant, obra fundacional del existencialismo",
                "sartre, existencialismo, filosofia",
            ),
            (
                "1921",
                "Tractatus Logico-Philosophicus",
                "Ludwig Wittgenstein",
                "Wittgenstein publica su única obra filosófica publicada en vida",
                "wittgenstein, logica, lenguaje",
            ),
            (
                "1637",
                "Discurso del Método",
                "René Descartes",
                "Descartes publica 'Cogito ergo sum', fundación de la filosofía moderna",
                "descartes, cogito, filosofia moderna",
            ),
            (
                "1651",
                "Leviatán",
                "Thomas Hobbes",
                "Hobbes publica su teoría del contrato social y el estado absoluto",
                "hobbes, politica, contrato social",
            ),
            (
                "1689",
                "Ensayo sobre el entendimiento humano",
                "John Locke",
                "Locke establece las bases del empirismo británico",
                "locke, empirismo, conocimiento",
            ),
        ]

        c.executemany(
            "INSERT INTO history (year, event, figures, context, tags) VALUES (?, ?, ?, ?, ?)",
            history,
        )

        # ── CONSTANTS ────────────────────────────────────────────────────
        constants = [
            (
                "Velocidad de la luz",
                "c",
                "299792458",
                "m/s",
                "fisica",
                "Exacta por definición desde 1983",
            ),
            (
                "Constante gravitacional",
                "G",
                "6.67430 × 10⁻¹¹",
                "N·m²/kg²",
                "fisica",
                "Medida con incertidumbre relativa de 2.2×10⁻⁵",
            ),
            (
                "Constante de Planck",
                "h",
                "6.62607015 × 10⁻³⁴",
                "J·s",
                "fisica",
                "Exacta por definición desde 2019",
            ),
            (
                "Constante de Boltzmann",
                "k_B",
                "1.380649 × 10⁻²³",
                "J/K",
                "fisica",
                "Exacta por definición desde 2019",
            ),
            (
                "Carga elemental",
                "e",
                "1.602176634 × 10⁻¹⁹",
                "C",
                "fisica",
                "Exacta por definición desde 2019",
            ),
            (
                "Número de Avogadro",
                "N_A",
                "6.02214076 × 10²³",
                "mol⁻¹",
                "quimica",
                "Exacta por definición desde 2019",
            ),
            (
                "Constante de los gases ideales",
                "R",
                "8.314462618",
                "J/(mol·K)",
                "quimica",
                "Exacta por definición",
            ),
            (
                "Gravedad terrestre estándar",
                "g",
                "9.80665",
                "m/s²",
                "fisica",
                "Valor estándar a nivel del mar",
            ),
            (
                "Permitividad del vacío",
                "ε₀",
                "8.8541878128 × 10⁻¹²",
                "F/m",
                "fisica",
                "Relacionada con c y μ₀",
            ),
            (
                "Permeabilidad del vacío",
                "μ₀",
                "1.25663706212 × 10⁻⁶",
                "N/A²",
                "fisica",
                "4π × 10⁻⁷ aproximadamente",
            ),
            (
                "Constante de Faraday",
                "F",
                "96485.33212",
                "C/mol",
                "quimica",
                "F = N_A · e",
            ),
            (
                "Constante de Stefan-Boltzmann",
                "σ",
                "5.670374419 × 10⁻⁸",
                "W/(m²·K⁴)",
                "fisica",
                "Radiación del cuerpo negro",
            ),
            (
                "Número de Euler",
                "e",
                "2.718281828459045...",
                "",
                "matematicas",
                "Base del logaritmo natural",
            ),
            (
                "Número áureo",
                "φ",
                "1.618033988749895...",
                "",
                "matematicas",
                "(1 + √5) / 2",
            ),
            (
                "Pi",
                "π",
                "3.141592653589793...",
                "",
                "matematicas",
                "Relación circunferencia/diámetro",
            ),
        ]

        c.executemany(
            "INSERT INTO constants (name, symbol, value, unit, field, notes) VALUES (?, ?, ?, ?, ?, ?)",
            constants,
        )

        # ── AUTHORS (Philosophy) ─────────────────────────────────────────
        authors = [
            (
                "Sócrates",
                "470 a.C.",
                "399 a.C.",
                "Filosofía clásica griega",
                "No escribió nada. Conocido por los diálogos de Platón.",
                "Mayéutica, ironía socrática, 'solo sé que no sé nada'",
                "Padre de la filosofía moral occidental. Condenado a beber cicuta.",
            ),
            (
                "Platón",
                "428 a.C.",
                "348 a.C.",
                "Idealismo",
                "La República, Fedón, Timeo, Simposio",
                "Teoría de las Ideas, mito de la caverna, alma tripartita",
                "Fundador de la Academia. Discípulo de Sócrates, maestro de Aristóteles.",
            ),
            (
                "Aristóteles",
                "384 a.C.",
                "322 a.C.",
                "Peripatetismo",
                "Metafísica, Ética a Nicómaco, Política, Organon",
                "Silogismo, cuatro causas, acto y potencia, hilemorfismo",
                "Fundador del Lyceum. Sistematizó la lógica formal.",
            ),
            (
                "René Descartes",
                "1596",
                "1650",
                "Racionalismo",
                "Discurso del Método, Meditaciones Metafísicas",
                "Cogito ergo sum, duda metódica, dualismo mente-cuerpo",
                "Padre de la filosofía moderna y la geometría analítica.",
            ),
            (
                "Immanuel Kant",
                "1724",
                "1804",
                "Idealismo trascendental",
                "Crítica de la razón pura, Crítica de la razón práctica, Crítica del juicio",
                "Imperativo categórico, cosa en sí, juicios sintéticos a priori, revolución copernicana",
                "Nunca salió de Königsberg. Intentó reconciliar racionalismo y empirismo.",
            ),
            (
                "G.W.F. Hegel",
                "1770",
                "1831",
                "Idealismo absoluto",
                "Fenomenología del espíritu, Ciencia de la lógica, Filosofía del derecho",
                "Dialéctica (tesis-antítesis-síntesis), espíritu absoluto, amo y esclavo",
                "Su filosofía influyó en Marx, Kierkegaard y toda la tradición continental.",
            ),
            (
                "Arthur Schopenhauer",
                "1788",
                "1860",
                "Pesimismo filosófico",
                "El mundo como voluntad y representación",
                "Voluntad como cosa en sí, negación de la voluntad, arte como liberación",
                "Influyó en Nietzsche, Freud, Wittgenstein y la filosofía oriental.",
            ),
            (
                "Søren Kierkegaard",
                "1813",
                "1855",
                "Existencialismo",
                "Temblor y terror, La enfermedad mortal, O lo uno o lo otro",
                "Salto de fe, angustia, estadio estético/ético/religioso",
                "Padre del existencialismo. Criticó el sistema hegeliano.",
            ),
            (
                "Friedrich Nietzsche",
                "1844",
                "1900",
                "Vitalismo / Nihilismo",
                "Así habló Zaratustra, Más allá del bien y del mal, La gaya ciencia",
                "Voluntad de poder, eterno retorno, Übermensch, muerte de Dios, transvaloración",
                "Crítico radical de la moral cristiana y la metafísica occidental.",
            ),
            (
                "Edmund Husserl",
                "1859",
                "1938",
                "Fenomenología",
                "Investigaciones lógicas, Ideas I, La crisis de las ciencias europeas",
                "Epoché, intencionalidad, reducción fenomenológica, mundo de la vida",
                "Fundador de la fenomenología. Maestro de Heidegger.",
            ),
            (
                "Martin Heidegger",
                "1889",
                "1976",
                "Fenomenología hermenéutica",
                "Ser y Tiempo, ¿Qué es la metafísica?, La pregunta por la técnica",
                "Dasein, ser-en-el-mundo, diferencia ontológica, ser-para-la-muerte, Gelassenheit",
                "Uno de los filósofos más influyentes del siglo XX. Controversia por su nazismo.",
            ),
            (
                "Jean-Paul Sartre",
                "1905",
                "1980",
                "Existencialismo",
                "El ser y la nada, El existencialismo es un humanismo, La náusea",
                "La existencia precede a la esencia, mala fe, libertad radical, el infierno son los otros",
                "Filósofo, novelista y dramaturgo. Rechazó el Nobel de Literatura.",
            ),
            (
                "Simone de Beauvoir",
                "1908",
                "1986",
                "Existencialismo / Feminismo",
                "El segundo sexo, La invitada, Memorias de una joven formal",
                "No se nace mujer, se llega a serlo, ética de la ambigüedad",
                "Pionera del feminismo contemporáneo. Compañera de Sartre.",
            ),
            (
                "Albert Camus",
                "1913",
                "1960",
                "Absurdismo",
                "El mito de Sísifo, El extranjero, La peste",
                "Lo absurdo, rebelión, Sísifo feliz",
                "Rechazó la etiqueta de existencialista. Nobel de Literatura 1957.",
            ),
            (
                "Ludwig Wittgenstein",
                "1889",
                "1951",
                "Filosofía del lenguaje",
                "Tractatus Logico-Philosophicus, Investigaciones filosóficas",
                "Límites del lenguaje, juegos de lenguaje, familia de conceptos, beetle in a box",
                "Dos etapas: lógica pictórica (Tractatus) y usos del lenguaje (Investigaciones).",
            ),
            (
                "Michel Foucault",
                "1926",
                "1984",
                "Postestructuralismo",
                "Vigilar y castigar, Historia de la sexualidad, Las palabras y las cosas",
                "Poder/saber, biopolítica, panóptico, episteme, arqueología del saber",
                "Analizó las relaciones entre poder, conocimiento y subjetividad.",
            ),
            (
                "Jacques Derrida",
                "1930",
                "2004",
                "Deconstrucción",
                "De la gramatología, La escritura y la diferencia, Márgenes de la filosofía",
                "Différance, deconstrucción, logocentrismo, falogocentrismo",
                "Fundador de la deconstrucción. Criticó la metafísica de la presencia.",
            ),
            (
                "John Locke",
                "1632",
                "1704",
                "Empirismo",
                "Ensayo sobre el entendimiento humano, Dos tratados sobre el gobierno civil",
                "Tabula rasa, derechos naturales, contrato social, separación de poderes",
                "Padre del liberalismo clásico. Influencia decisiva en la Ilustración.",
            ),
            (
                "David Hume",
                "1711",
                "1776",
                "Empirismo / Escepticismo",
                "Tratado de la naturaleza humana, Investigación sobre el entendimiento humano",
                "Problema de la inducción, impresiones e ideas, guillotina de Hume",
                "Despertó a Kant de su sueño dogmático. Escéptico radical.",
            ),
            (
                "Baruch Spinoza",
                "1632",
                "1677",
                "Racionalismo / Panteísmo",
                "Ética demostrada según el orden geométrico, Tratado teológico-político",
                "Deus sive Natura, determinismo, conatus, tres tipos de conocimiento",
                "Excomulgado por la comunidad judía de Ámsterdam. Dios = Naturaleza.",
            ),
        ]

        c.executemany(
            "INSERT INTO authors (name, birth, death, movement, key_works, concepts, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            authors,
        )

        # ── CONCEPTS ─────────────────────────────────────────────────────
        concepts = [
            (
                "Dasein",
                "filosofia",
                "Término de Heidegger para designar al ser humano como el ente cuya esencia es existir. Se caracteriza por el ser-en-el-mundo, la facticidad, la proyección y el ser-para-la-muerte.",
                "Heidegger, Ser y Tiempo, ontología",
                "",
            ),
            (
                "Imperativo categórico",
                "filosofia",
                "Principio moral de Kant: 'Obra solo según aquella máxima por la cual puedas querer que al mismo tiempo se convierta en ley universal'. No depende de deseos particulares.",
                "Kant, ética, moral",
                "",
            ),
            (
                "Dialéctica",
                "filosofia",
                "Método filosófico que procede mediante la contradicción y su superación. En Hegel: tesis → antítesis → síntesis. En Marx: dialéctica materialista aplicada a la historia.",
                "Hegel, Marx, contradicción",
                "",
            ),
            (
                "Cogito",
                "filosofia",
                "'Cogito ergo sum' (Pienso, luego existo). Punto de partida indudable de Descartes. La duda misma prueba la existencia del sujeto que duda.",
                "Descartes, racionalismo, duda metódica",
                "",
            ),
            (
                "Voluntad de poder",
                "filosofia",
                "Concepto central de Nietzsche: la fuerza fundamental que impulsa toda vida, no como deseo de dominación sino como impulso creativo de autoafirmación y superación.",
                "Nietzsche, vitalismo, Übermensch",
                "",
            ),
            (
                "Epoché",
                "filosofia",
                "Suspensión del juicio propuesta por Husserl. Poner entre paréntesis las creencias naturales sobre el mundo para acceder a la experiencia pura de la conciencia.",
                "Husserl, fenomenología, reducción",
                "",
            ),
            (
                "Deconstrucción",
                "filosofia",
                "Método de Derrida que revela las contradicciones internas de los textos filosóficos. No es destrucción sino lectura atenta de los márgenes y exclusiones del texto.",
                "Derrida, différance, logocentrismo",
                "",
            ),
            (
                "Tabula rasa",
                "filosofia",
                "Concepto de Locke: la mente humana al nacer es como una pizarra en blanco. Todo conocimiento proviene de la experiencia. Fundamento del empirismo.",
                "Locke, empirismo, conocimiento",
                "",
            ),
            (
                "Cosa en sí",
                "filosofia",
                "El Ding an sich de Kant: la realidad tal como es independientemente de nuestra percepción. Es incognoscible porque solo conocemos fenómenos, no noúmenos.",
                "Kant, noúmeno, fenómeno",
                "",
            ),
            (
                "Eterno retorno",
                "filosofia",
                "Idea de Nietzsche: ¿podrías desear vivir esta misma vida infinitas veces? Es la prueba suprema de afirmación de la vida. Solo el Übermensch puede afirmarlo.",
                "Nietzsche, afirmación, Übermensch",
                "",
            ),
            (
                "Biopolítica",
                "filosofia",
                "Concepto de Foucault: el ejercicio del poder sobre la vida de las poblaciones. Incluye control de natalidad, salud pública, higiene. El poder ya no solo mata, sino que administra la vida.",
                "Foucault, poder, poblaciones",
                "",
            ),
            (
                "Mala fe",
                "filosofia",
                "Concepto de Sartre: el autoengaño mediante el cual el ser humano niega su libertad radical. Pretender que somos algo fijo (un 'ser-en-sí') en lugar de proyecto abierto.",
                "Sartre, existencialismo, libertad",
                "",
            ),
            (
                "Silogismo",
                "filosofia",
                "Forma de razonamiento deductivo de Aristóteles: premisa mayor + premisa menor → conclusión. Ejemplo: Todos los hombres son mortales. Sócrates es hombre. → Sócrates es mortal.",
                "Aristóteles, lógica, deducción",
                "",
            ),
            (
                "Entropía",
                "fisica",
                "Medida del desorden o la irreversibilidad de un sistema. Segunda ley: la entropía total del universo siempre aumenta. Boltzmann: S = k_B · ln(Ω).",
                "Termodinámica, Boltzmann, desorden",
                "",
            ),
            (
                "Función de onda",
                "fisica",
                "En mecánica cuántica, ψ describe el estado de un sistema. |ψ|² da la probabilidad de encontrar una partícula en una posición. Colapsa al medir.",
                "Mecánica cuántica, Schrödinger, probabilidad",
                "",
            ),
            (
                "Teorema de incompletitud de Gödel",
                "matematicas",
                "En cualquier sistema formal consistente y suficientemente expresivo, existen proposiciones verdaderas que no pueden demostrarse dentro del sistema.",
                "Gödel, lógica, límites formales",
                "",
            ),
        ]

        c.executemany(
            "INSERT INTO concepts (term, field, definition, related, notes) VALUES (?, ?, ?, ?, ?)",
            concepts,
        )

        # ── REBUILD FTS INDEXES ──────────────────────────────────────────
        c.execute(
            "INSERT INTO fts_formulas(rowid, category, name, formula, variables, notes) SELECT id, category, name, formula, variables, notes FROM formulas"
        )
        c.execute(
            "INSERT INTO fts_history(rowid, year, event, figures, context, tags) SELECT id, year, event, figures, context, tags FROM history"
        )
        c.execute(
            "INSERT INTO fts_constants(rowid, name, symbol, value, unit, field, notes) SELECT id, name, symbol, value, unit, field, notes FROM constants"
        )
        c.execute(
            "INSERT INTO fts_authors(rowid, name, birth, death, movement, key_works, concepts, notes) SELECT id, name, birth, death, movement, key_works, concepts, notes FROM authors"
        )
        c.execute(
            "INSERT INTO fts_concepts(rowid, term, field, definition, related, notes) SELECT id, term, field, definition, related, notes FROM concepts"
        )
        c.commit()

    # ─── QUERY METHODS ────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_fts(query: str) -> str:
        """Remove FTS5 special characters that cause syntax errors."""
        for ch in '=<>(){}[]:"\\/*+-~^':
            query = query.replace(ch, " ")
        tokens = [t for t in query.split() if len(t) >= 2]
        return " ".join(tokens)

    def search_formulas(self, query: str, limit: int = 3) -> list[dict]:
        c = self._connect()
        safe = self._sanitize_fts(query)
        if not safe:
            return []
        ids = [
            r[0]
            for r in c.execute(
                "SELECT rowid FROM fts_formulas WHERE fts_formulas MATCH ? LIMIT ?",
                (safe, limit),
            ).fetchall()
        ]
        if not ids:
            return []
        rows = c.execute(
            f"SELECT id, category, name, formula, variables, notes FROM formulas WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        return [dict(r) for r in rows]

    def search_history(self, query: str, limit: int = 3) -> list[dict]:
        c = self._connect()
        safe = self._sanitize_fts(query)
        if not safe:
            return []
        ids = [
            r[0]
            for r in c.execute(
                "SELECT rowid FROM fts_history WHERE fts_history MATCH ? LIMIT ?",
                (safe, limit),
            ).fetchall()
        ]
        if not ids:
            return []
        rows = c.execute(
            f"SELECT id, year, event, figures, context, tags FROM history WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        return [dict(r) for r in rows]

    def search_constants(self, query: str, limit: int = 3) -> list[dict]:
        c = self._connect()
        safe = self._sanitize_fts(query)
        if not safe:
            return []
        ids = [
            r[0]
            for r in c.execute(
                "SELECT rowid FROM fts_constants WHERE fts_constants MATCH ? LIMIT ?",
                (safe, limit),
            ).fetchall()
        ]
        if not ids:
            return []
        rows = c.execute(
            f"SELECT id, name, symbol, value, unit, field, notes FROM constants WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        return [dict(r) for r in rows]

    def search_authors(self, query: str, limit: int = 3) -> list[dict]:
        c = self._connect()
        safe = self._sanitize_fts(query)
        if not safe:
            return []
        ids = [
            r[0]
            for r in c.execute(
                "SELECT rowid FROM fts_authors WHERE fts_authors MATCH ? LIMIT ?",
                (safe, limit),
            ).fetchall()
        ]
        if not ids:
            return []
        rows = c.execute(
            f"SELECT id, name, birth, death, movement, key_works, concepts, notes FROM authors WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        return [dict(r) for r in rows]

    def search_concepts(self, query: str, limit: int = 3) -> list[dict]:
        c = self._connect()
        safe = self._sanitize_fts(query)
        if not safe:
            return []
        ids = [
            r[0]
            for r in c.execute(
                "SELECT rowid FROM fts_concepts WHERE fts_concepts MATCH ? LIMIT ?",
                (safe, limit),
            ).fetchall()
        ]
        if not ids:
            return []
        rows = c.execute(
            f"SELECT id, term, field, definition, related, notes FROM concepts WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        return [dict(r) for r in rows]

    def search_all(self, query: str, limit: int = 3) -> dict[str, list]:
        """Search all tables and return combined results."""
        return {
            "formulas": self.search_formulas(query, limit),
            "history": self.search_history(query, limit),
            "constants": self.search_constants(query, limit),
            "authors": self.search_authors(query, limit),
            "concepts": self.search_concepts(query, limit),
        }

    def format_results(self, results: dict[str, list]) -> str:
        """Format search results as a text block for prompt injection."""
        parts = []

        for category, items in results.items():
            if not items:
                continue

            header = f"\n═══ {category.upper()} ═══"
            parts.append(header)

            for item in items:
                if category == "formulas":
                    parts.append(f"• {item['name']}: {item['formula']}")
                    if item.get("variables"):
                        parts.append(f"  Variables: {item['variables']}")
                    if item.get("notes"):
                        parts.append(f"  Nota: {item['notes']}")

                elif category == "history":
                    parts.append(f"• [{item['year']}] {item['event']}")
                    if item.get("figures"):
                        parts.append(f"  Figuras: {item['figures']}")
                    if item.get("context"):
                        parts.append(f"  Contexto: {item['context']}")

                elif category == "constants":
                    sym = f" ({item['symbol']})" if item.get("symbol") else ""
                    unit = f" {item['unit']}" if item.get("unit") else ""
                    parts.append(f"• {item['name']}{sym} = {item['value']}{unit}")

                elif category == "authors":
                    dates = ""
                    if item.get("birth"):
                        dates = f" ({item['birth']}"
                        if item.get("death"):
                            dates += f"–{item['death']}"
                        dates += ")"
                    parts.append(f"• {item['name']}{dates}")
                    if item.get("movement"):
                        parts.append(f"  Movimiento: {item['movement']}")
                    if item.get("key_works"):
                        parts.append(f"  Obras: {item['key_works']}")
                    if item.get("concepts"):
                        parts.append(f"  Conceptos: {item['concepts']}")

                elif category == "concepts":
                    parts.append(f"• {item['term']} ({item['field']})")
                    parts.append(f"  {item['definition']}")

        if not parts:
            return ""

        return "\n".join(parts)

    def stats(self) -> dict:
        c = self._connect()
        return {
            "formulas": c.execute("SELECT COUNT(*) FROM formulas").fetchone()[0],
            "history": c.execute("SELECT COUNT(*) FROM history").fetchone()[0],
            "constants": c.execute("SELECT COUNT(*) FROM constants").fetchone()[0],
            "authors": c.execute("SELECT COUNT(*) FROM authors").fetchone()[0],
            "concepts": c.execute("SELECT COUNT(*) FROM concepts").fetchone()[0],
        }

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ─── MODULE-LEVEL CONVENIENCE ────────────────────────────────────────────────

_knowledge: KnowledgeDB | None = None


def get_knowledge() -> KnowledgeDB:
    global _knowledge
    if _knowledge is None:
        _knowledge = KnowledgeDB()
    return _knowledge


def search_knowledge(query: str, limit: int = 3) -> str:
    """Search all tables and return formatted results."""
    kb = get_knowledge()
    results = kb.search_all(query, limit)
    return kb.format_results(results)


def knowledge_stats() -> dict:
    return get_knowledge().stats()


if __name__ == "__main__":
    import sys

    kb = get_knowledge()

    if len(sys.argv) > 1 and sys.argv[1] == "--stats":
        print("  Knowledge Base Statistics:")
        for k, v in kb.stats().items():
            print(f"    {k}: {v} entries")
    elif len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        results = kb.search_all(query)
        formatted = kb.format_results(results)
        if formatted:
            print(f"\n  Resultados para: '{query}'\n")
            print(formatted)
        else:
            print(f"\n  No se encontraron resultados para: '{query}'")
    else:
        print("  Uso: python knowledge_db.py [--stats|<query>]")

    kb.close()
