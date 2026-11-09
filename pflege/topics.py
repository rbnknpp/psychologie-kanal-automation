"""Themen fuer den Kanal „Pflege mit Plan“ (@pflege-mit-plan), von Robin am 09.11.2026 freigegeben.

Reihe "angehoerige": Fakten NUR von der verlinkten Seite auf pflege-mit-plan.com (wird zur Laufzeit gelesen).
Reihe "azubi": Lehrbuch-Grundwissen fuer Pflege-Azubis, ohne Medikamente/Dosierungen/aerztliche Entscheidungen.
amazon = Suchbegriff fuer den Amazon-Partnerlink (Buecher zum Thema).
"""

SITE = "https://pflege-mit-plan.com"

ANGEHOERIGE = [
    ("pflegegrad-beantragen", "Pflegegrad beantragen in 4 Schritten", "/ratgeber/pflegegrad-beantragen", "Pflegegrad beantragen Ratgeber"),
    ("widerspruch", "Pflegegrad abgelehnt – Widerspruch innerhalb eines Monats", "/ratgeber/pflegegrad-abgelehnt-widerspruch", "Widerspruch Pflegegrad Ratgeber"),
    ("md-begutachtung", "Begutachtung durch den Medizinischen Dienst vorbereiten", "/ratgeber/md-begutachtung-vorbereiten", "Begutachtung Pflegegrad vorbereiten"),
    ("module", "Die 6 Module der Begutachtung einfach erklärt", "/pflegegrad-rechner", "Pflegegrad Begutachtung Buch"),
    ("hoeherstufung", "Höherstufung: wann sie sich lohnt", "/ratgeber/pflegegrad-erhoehen", "Pflegegrad Ratgeber Angehörige"),
    ("geld-pro-pflegegrad", "Wie viel Geld gibt es bei Pflegegrad 1 bis 5?", "/ratgeber/wie-viel-geld-pro-pflegegrad", "Pflegeversicherung Ratgeber"),
    ("pflegegeld-sachleistung", "Pflegegeld, Sachleistung oder Kombination?", "/ratgeber/pflegegeld-oder-pflegedienst", "Pflegegeld Ratgeber"),
    ("entlastungsbetrag", "Entlastungsbetrag – wofür und wie", "/entlastungsbetrag", "Pflege zu Hause Ratgeber Angehörige"),
    ("verhinderungspflege", "Verhinderungspflege und Kurzzeitpflege", "/verhinderungspflege", "Pflegende Angehörige Ratgeber"),
    ("tagespflege", "Tagespflege – was sie bringt", "/lexikon/tagespflege", "Pflege zu Hause Ratgeber"),
    ("pflegebox", "Pflegebox ohne Zuzahlung", "/pflegebox-beantragen", "Pflegehilfsmittel"),
    ("hausnotruf", "Hausnotruf mit Zuschuss", "/hausnotruf-zuschuss", "Hausnotruf Senioren"),
    ("badumbau", "Badumbau mit Zuschuss der Pflegekasse", "/badumbau-pflegekasse", "barrierefreies Bad Senioren"),
    ("treppenlift", "Treppenlift mit Zuschuss", "/treppenlift-zuschuss", "barrierefrei wohnen Ratgeber"),
    ("beratungsbesuch", "Beratungsbesuch nach Paragraf 37.3", "/ratgeber/beratungsbesuch-37-3", "Pflegegeld Ratgeber"),
    ("krankenhaus-entlassung", "Entlassung aus dem Krankenhaus – Pflege organisieren", "/ratgeber/entlassung-krankenhaus-pflege", "Pflege zu Hause organisieren"),
    ("demenz", "Demenz zu Hause – erste Schritte", "/ratgeber/demenz-zu-hause-pflegen", "Demenz Ratgeber Angehörige"),
    ("pflege-und-beruf", "Pflege und Beruf: Pflegezeit und Familienpflegezeit", "/ratgeber/pflege-und-beruf", "Pflege und Beruf Ratgeber"),
    ("rente", "Rentenpunkte für pflegende Angehörige", "/ratgeber/rente-fuer-pflegende-angehoerige", "pflegende Angehörige Ratgeber"),
    ("steuern", "Pflegepauschbetrag bei der Steuer", "/ratgeber/steuern-pflege", "Steuern Pflege Ratgeber"),
    ("vorsorge", "Vorsorgevollmacht und Patientenverfügung", "/ratgeber/vorsorgevollmacht-patientenverfuegung", "Vorsorgevollmacht Patientenverfügung Buch"),
    ("pflegekurs", "Kostenloser Pflegekurs für Angehörige", "/ratgeber/pflegekurs-kostenlos", "Pflege lernen Angehörige Buch"),
    ("pflegedienst-finden", "Ambulanten Pflegedienst finden – Checkliste", "/ratgeber/pflegedienst-finden", "ambulante Pflege Ratgeber"),
    ("entlastung", "Überlastet? So entlasten Sie sich", "/ratgeber/pflegende-angehoerige-entlasten", "pflegende Angehörige Selbstfürsorge"),
    ("pflegestuetzpunkt", "Pflegestützpunkt – kostenlose Beratung vor Ort", "/pflegestuetzpunkte-saarland", "Pflege Ratgeber Angehörige"),
]

AZUBI = [
    ("pflegeprozess", "Pflegeprozess in 6 Schritten", "Pflegeprozess Lehrbuch"),
    ("sis", "SIS und Strukturmodell einfach erklärt", "Strukturmodell SIS Pflege"),
    ("pflegeplanung", "Pflegeplanung schreiben – typische Fehler", "Pflegeplanung Buch"),
    ("dokumentation", "Pflegedokumentation: was rein muss", "Pflegedokumentation Buch"),
    ("uebergabe", "Übergabe richtig machen", "Pflegeausbildung Praxis Buch"),
    ("fachbegriffe", "Fachbegriffe, die in jeder Prüfung kommen", "medizinische Fachbegriffe Pflege"),
    ("pruefung-lernen", "Prüfungsvorbereitung: so lernst du effektiv", "Pflegefachfrau Prüfung Vorbereitung"),
    ("dekubitus", "Dekubitusprophylaxe", "Prophylaxen Pflege Buch"),
    ("sturz", "Sturzprophylaxe", "Prophylaxen Pflege Buch"),
    ("pneumonie", "Pneumonieprophylaxe", "Prophylaxen Pflege Buch"),
    ("thrombose", "Thromboseprophylaxe", "Prophylaxen Pflege Buch"),
    ("kontraktur", "Kontrakturenprophylaxe", "Prophylaxen Pflege Buch"),
    ("intertrigo", "Intertrigoprophylaxe", "Prophylaxen Pflege Buch"),
    ("soor-parotitis", "Soor- und Parotitisprophylaxe", "Prophylaxen Pflege Buch"),
    ("aspiration", "Aspirationsprophylaxe", "Prophylaxen Pflege Buch"),
    ("vitalzeichen", "Vitalzeichen und ihre Normalwerte", "Pflege Lehrbuch Vitalzeichen"),
    ("bewusstsein", "Bewusstsein beobachten", "Krankenbeobachtung Pflege Buch"),
    ("haut", "Haut beobachten", "Krankenbeobachtung Pflege Buch"),
    ("schmerz", "Schmerz einschätzen mit Schmerzskalen", "Schmerzmanagement Pflege Buch"),
    ("bilanz", "Flüssigkeitsbilanz", "Pflege Lehrbuch"),
    ("haendedesinfektion", "Händedesinfektion – die 5 Momente", "Hygiene Pflege Buch"),
    ("hygiene", "Hygiene und Schutzkleidung", "Hygiene Pflege Buch"),
    ("lagerung", "Lagerung und Mobilisation – Grundprinzipien", "Lagerung Mobilisation Pflege"),
    ("kinaesthetik", "Kinästhetik kurz erklärt", "Kinästhetik Buch"),
    ("inkontinenz", "Inkontinenz: Formen und Versorgung", "Inkontinenz Pflege Buch"),
    ("mangelernaehrung", "Mangelernährung erkennen", "Ernährung Pflege Buch"),
    ("demenz-kommunikation", "Menschen mit Demenz begleiten – Kommunikation", "Demenz Pflege Kommunikation Buch"),
    ("validation", "Validation nach Naomi Feil", "Validation Naomi Feil"),
    ("palliativ", "Palliativpflege – Grundhaltung", "Palliativpflege Buch"),
    ("schweigepflicht", "Schweigepflicht und Datenschutz in der Pflege", "Pflegerecht Buch"),
    ("fem", "Freiheitsentziehende Maßnahmen – was gilt", "Pflegerecht Buch"),
    ("delegation", "Delegation: was darf die Pflege?", "Pflegerecht Buch"),
    ("behandlungspflege", "Behandlungspflege und Grundpflege", "ambulante Pflege Buch"),
    ("muster12", "Verordnung häuslicher Krankenpflege (Muster 12)", "häusliche Krankenpflege Buch"),
    ("expertenstandards", "Expertenstandards im Überblick", "Expertenstandards Pflege Buch"),
]


def alle():
    out = [{"slug": s, "titel": t, "reihe": "angehoerige", "quelle": SITE + p, "amazon": a} for s, t, p, a in ANGEHOERIGE]
    out += [{"slug": s, "titel": t, "reihe": "azubi", "quelle": SITE, "amazon": a} for s, t, a in AZUBI]
    return out
