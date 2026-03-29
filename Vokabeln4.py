import csv, os, json, math  
  
VOKABEL_DATEI = "vokabeln.csv"   
PROGRESS_DIR = "progress_save"   
os.makedirs(PROGRESS_DIR, exist_ok=True)   
  
def load_progress_block(block_key):   
    if not os.path.exists("progress_blocks.json"):   
        return 0  
    with open("progress_blocks.json", "r") as f:   
        try:   
            data = json.load(f)   
        except:   
            return 0  
    return data.get(block_key, 0)   
  
def save_progress_block(block_key, idx):   
    if os.path.exists("progress_blocks.json"):   
        with open("progress_blocks.json", "r") as f:   
            try:   
                data = json.load(f)   
            except:   
                data = {}   
    else:   
        data = {}   
    data[block_key] = idx  
    with open("progress_blocks.json", "w") as f:   
        json.dump(data, f)   
  
def lade_vokabeln_full():   
    vokabeln = []   
    if not os.path.exists(VOKABEL_DATEI):   
        return vokabeln  
    with open(VOKABEL_DATEI, newline="", encoding="utf-8") as f:   
        reader = csv.DictReader(f)   
        for row in reader:   
            row['richtig'] = int(row.get('richtig',0))   
            row['falsch']  = int(row.get('falsch',0))   
            vokabeln.append(row)   
    return vokabeln  
  
def speichere_vokabeln_full(vokabeln):   
    with open(VOKABEL_DATEI, "w", newline="", encoding="utf-8") as f:   
        fieldnames = ['fremdsprache', 'deutsch', 'deklination', 'lektion', 'richtig', 'falsch']   
        writer = csv.DictWriter(f, fieldnames=fieldnames)   
        writer.writeheader()   
        for v in vokabeln:   
            writer.writerow(v)   
  
def frage_lektionen(master_vokabeln):   
    alle_lektionen = sorted(set(v["lektion"] for v in master_vokabeln))   
    print("Welche Lektionen willst du üben? z.B. '1' oder '2,3,4' oder 'alle'")   
    print("Verfügbare Lektionen:", ", ".join(alle_lektionen))   
    ein = input("Deine Wahl: ").replace(" ", "")   
    if ein.lower() == "alle":   
        return alle_lektionen  
    gew = ein.split(",")   
    return gew  
  
def save_progress(progress_file, idx):   
    with open(os.path.join(PROGRESS_DIR, progress_file), "w") as f:   
        json.dump({"index": idx}, f)   
  
def load_progress(progress_file):   
    try:   
        with open(os.path.join(PROGRESS_DIR, progress_file)) as f:   
            return json.load(f)["index"]   
    except:   
        return 0  
  
def chunks(lst, n):   
    for i in range(0, len(lst), n):   
        yield lst[i:i+n]   
  
def find_vokabel(master_vokabeln, such_vokabel):   
    # sucht ein Dict aus master_vokabeln das gleiches Fremdwort UND Lektion hat (zur sicheren Syncronisation)   
    for v in master_vokabeln:   
        if v['fremdsprache'] == such_vokabel['fremdsprache'] and v['lektion'] == such_vokabel['lektion']:   
            return v  
    return None  
  
def kartei_modus(master_vokabeln):   
    print("*** KARTEI-MODUS ***")   
    lektionen = frage_lektionen(master_vokabeln)   
    progress_file = "progress_kartei.json_" + "_".join(lektionen)   
    # nur aktuelle Lektionen als Ansicht, bearbeitet werden aber immer die Objekte in master_vokabeln!   
    targets = [v for v in master_vokabeln if v['lektion'] in lektionen]   
    falsch_list = []   
    idx = load_progress(progress_file)   
    while idx < len(targets):   
        v = targets[idx]   
        print(f"Vokabel {idx+1}/{len(targets)} (Lektion {v['lektion']})")   
        if v['deklination']:   
            print("(Deklinations-Info: %s)" % v['deklination'])   
        antwort = input(f"{v['fremdsprache']} -> ").strip()   
        master_ref = find_vokabel(master_vokabeln, v)   
        if antwort.lower() == v['deutsch'].lower():   
            print("Richtig!")   
            v['richtig'] += 1  
            if master_ref: master_ref['richtig'] = v['richtig']   
        else:   
            print(f"Falsch! Die richtige Antwort war: {v['deutsch']}")   
            v['falsch'] += 1  
            if master_ref: master_ref['falsch'] = v['falsch']   
            falsch_list.append(v)   
        idx += 1  
        save_progress(progress_file, idx)   
        speichere_vokabeln_full(master_vokabeln)   
    print("Fertig! Falsch beantwortete Vokabeln (nochmal üben):")   
    for v in falsch_list:   
        print(f"- {v['fremdsprache']} ({v['deutsch']})")   
    input("ENTER = weiter")   
  
def abschreib_modus(master_vokabeln):   
    print("*** ABSCHREIB-MODUS ***")   
    lektionen = frage_lektionen(master_vokabeln)   
    progress_file = "progress_abschreiben.json_" + "_".join(lektionen)   
    targets = [v for v in master_vokabeln if v['lektion'] in lektionen]   
    idx = load_progress(progress_file)   
    while idx < len(targets):   
        v = targets[idx]   
        print(f"{v['fremdsprache']} | {v['deutsch']} | {v['deklination']} (Lektion {v['lektion']})")   
        input("Bitte alles abschreiben und mit ENTER fortfahren!")   
        idx += 1  
        save_progress(progress_file, idx)   
    print("Liste durchgearbeitet! ENTER = weiter")   
    input()   
  
def deklination_modus(master_vokabeln):   
    print("*** DEKLINATIONSMODUS ***")   
    lektionen = frage_lektionen(master_vokabeln)   
    progress_file = "progress_deklination.json_" + "_".join(lektionen)   
    targets = [v for v in master_vokabeln if v['lektion'] in lektionen]   
    falsch_list = []   
    idx = load_progress(progress_file)   
    while idx < len(targets):   
        v = targets[idx]   
        frage = f"{v['fremdsprache']} ({v['deutsch']})"   
        antwort = input(f"{frage} –> Deklinationsinfo: ").strip()   
        master_ref = find_vokabel(master_vokabeln, v)   
        if antwort.lower() == (v['deklination'] or "").lower():   
            print("Richtig!")   
            v['richtig'] += 1  
            if master_ref: master_ref['richtig'] = v['richtig']   
        else:   
            print(f"Falsch! Die richtige Antwort war: {v['deklination']}")   
            v['falsch'] += 1  
            if master_ref: master_ref['falsch'] = v['falsch']   
            falsch_list.append(v)   
        idx += 1  
        save_progress(progress_file, idx)   
        speichere_vokabeln_full(master_vokabeln)   
    print("Fertig! Diese Deklinationsinfos waren falsch:")   
    for v in falsch_list:   
        print(f"- {v['fremdsprache']} ({v['deutsch']}) -> {v['deklination']}")   
    input("ENTER = weiter")   
  
def fuenfer_modus(master_vokabeln):
    print("*** Block-Modus für Häppchenlernen ***")
    lektionen = frage_lektionen(master_vokabeln)
    targets = [v for v in master_vokabeln if v['lektion'] in lektionen]
    # Blockgröße auswählen
    while True:
        blocksize_input = input("Wie groß sollen die Blöcke sein? (z.B. 5): ").strip()
        if blocksize_input.isdigit() and int(blocksize_input) > 0:
            blocksize = int(blocksize_input)
            break
        print("Ungültige Eingabe!")
    blöcke = list(chunks(targets, blocksize))
    total_blocks = len(blöcke)
    bl_names = [f"{i+1}" for i in range(total_blocks)]
    print("Blöcke verfügbar:")
    print(", ".join(f"Block {n}" for n in bl_names))
    auswahl = input("Welche Blöcke? (z.B. 1,3,5 oder 'alle'): ").replace(" ", "")
    if auswahl.lower() == "alle":
        block_indices = list(range(total_blocks))
    else:
        block_indices = [int(idx)-1 for idx in auswahl.split(",") if idx.isdigit() and 1 <= int(idx) <= total_blocks]

    def clear_screen():
        os.system('cls' if os.name == 'nt' else 'clear')

    for idx in block_indices:
        block = blöcke[idx]
        block_key = f"lektion{'-'.join(lektionen)}_block{idx+1}_size{blocksize}"
        start_idx = load_progress_block(block_key)
        print(f"\n-- Block {idx+1} ({(idx*blocksize)+1}-{min((idx+1)*blocksize, len(targets))} der Auswahl) --")
        wiederholungen = input("Wie oft diesen Block wiederholen? (Zahl, leer = unendlich bis STOP): ")
        wiederholungen = int(wiederholungen) if wiederholungen.isdigit() else 999999999
        runs = 0
        falsch_block = []
        while True:
            runs += 1
            print(f"Blockrunde {runs} (STOP für Abbruch)")
            for i in range(start_idx, len(block)):
                v = block[i]
                print(f"(Deklinations-Info: {v['deklination']})" if v['deklination'] else "")
                antwort = input(f"{v['fremdsprache']} -> ").strip()
                master_ref = find_vokabel(master_vokabeln, v)
                if antwort.upper() == "STOP":
                    start_idx = i
                    save_progress_block(block_key, start_idx)
                    break
                if antwort.lower() == v['deutsch'].lower():
                    print("Richtig!")
                    v['richtig'] += 1
                    if master_ref: master_ref['richtig'] = v['richtig']
                    # speichern bevor Bildschirm geleert wird
                    save_progress_block(block_key, i+1)
                    speichere_vokabeln_full(master_vokabeln)
                    clear_screen()  # sofort leeren und nächstes Wort
                    continue
                else:
                    print(f"Falsch! Die richtige Antwort war: {v['deutsch']}")
                    v['falsch'] += 1
                    if master_ref: master_ref['falsch'] = v['falsch']
                    falsch_block.append(v)
                    save_progress_block(block_key, i+1)
                    speichere_vokabeln_full(master_vokabeln)
                    input("ENTER = weiter")  # auf ENTER warten, dann leeren
                    clear_screen()
            else:
                start_idx = 0
                save_progress_block(block_key, 0)
            if runs >= wiederholungen or (antwort.upper() == "STOP"):
                break

            # Fehler-Fragerunde
            if falsch_block:
                noch_fehlerrunde = input("Fehlervokabeln nochmals lernen? (j/n): ").strip().lower()
                if noch_fehlerrunde == "j":
                    # Duplikate entfernen:
                    unique_falsch = []
                    already = set()
                    for v in falsch_block:
                        key = (v['fremdsprache'], v['deutsch'], v['lektion'])
                        if key not in already:
                            unique_falsch.append(v)
                            already.add(key)
                    falsch_block_sortiert = sorted(unique_falsch, key=lambda v: v['falsch'], reverse=True)
                    fail_blocks = math.ceil(len(falsch_block_sortiert)/5)
                    for fb in range(fail_blocks):
                        teil = falsch_block_sortiert[fb*5:(fb+1)*5]
                        if not teil: continue
                        print(f"-- Fehlerblock {fb+1} --")
                        w_fehler = input("Wie oft diesen Fehlerblock wiederholen? (Zahl, ENTER=infty): ")
                        w_fehler = int(w_fehler) if w_fehler.isdigit() else 999999999
                        ff_runs = 0
                        while ff_runs < w_fehler:
                            ff_runs += 1
                            falsch_zw = []
                            for v in teil:
                                print(f"(Deklinations-Info: {v['deklination']})" if v['deklination'] else "")
                                antwort = input(f"{v['fremdsprache']} -> ").strip()
                                master_ref = find_vokabel(master_vokabeln, v)
                                if antwort.lower() == v['deutsch'].lower():
                                    print("Richtig!")
                                    v['richtig'] += 1
                                    if master_ref: master_ref['richtig'] = v['richtig']
                                    speichere_vokabeln_full(master_vokabeln)
                                    clear_screen()  # sofort leeren
                                    continue
                                else:
                                    print(f"Falsch! Die richtige Antwort war: {v['deutsch']}")
                                    v['falsch'] += 1
                                    if master_ref: master_ref['falsch'] = v['falsch']
                                    falsch_zw.append(v)
                                    speichere_vokabeln_full(master_vokabeln)
                                    input("ENTER = weiter")
                                    clear_screen()
                            if not falsch_zw:
                                break
                        print(f"-- Fehlerblock {fb+1} fertig --")
            print(f"-- Block {idx+1} abgeschlossen --") 
  
def fehler_modus(master_vokabeln):   
    print("*** FEHLER-MODUS ***")   
    lektionen = frage_lektionen(master_vokabeln)   
    progress_file = "progress_fehler.json_" + "_".join(lektionen)   
    targets = [v for v in master_vokabeln if v['lektion'] in lektionen and v['falsch'] > 0]   
    targets = sorted(targets, key=lambda v: v['falsch'], reverse=True)   
    idx = load_progress(progress_file)   
    while idx < len(targets):   
        v = targets[idx]   
        print(f"(Deklinations-Info: {v['deklination']})" if v['deklination'] else "")   
        antwort = input(f"{v['fremdsprache']} -> ").strip()   
        master_ref = find_vokabel(master_vokabeln, v)   
        if antwort.lower() == v['deutsch'].lower():   
            print("Richtig!")   
            v['richtig'] += 1  
            if master_ref: master_ref['richtig'] = v['richtig']   
        else:   
            print(f"Falsch! Die richtige Antwort war: {v['deutsch']}")   
            v['falsch'] += 1  
            if master_ref: master_ref['falsch'] = v['falsch']   
        idx += 1  
        save_progress(progress_file, idx)   
        speichere_vokabeln_full(master_vokabeln)   
    print("Alle Fehler-Vokabeln durch! ENTER = weiter")   
    input()   
  
def main():   
    if not os.path.exists(VOKABEL_DATEI):   
        print("Datei vokabeln.csv fehlt! Bitte im Format:\nfremdsprache,deutsch,deklination,lektion,richtig,falsch")   
        return  
  
    master_vokabeln = lade_vokabeln_full()   
  
    while True:   
        print("\nWelchen Modus möchtest du?")   
        print("[1] Kartei-Karten")   
        print("[2] Abschreib-Modus")   
        print("[3] Deklinations-Modus")   
        print("[4] 5er-Häppchen-Modus")   
        print("[5] Meiste Fehler zuerst")   
        print("[q] Beenden")   
        modus = input("Auswahl: ")   
        if modus == "1":   
            kartei_modus(master_vokabeln)   
        elif modus == "2":   
            abschreib_modus(master_vokabeln)   
        elif modus == "3":   
            deklination_modus(master_vokabeln)   
        elif modus == "4":   
            fuenfer_modus(master_vokabeln)   
        elif modus == "5":   
            fehler_modus(master_vokabeln)   
        elif modus.lower() == "q":   
            break  
        else:   
            print("Ungültige Eingabe!")   
  
if __name__ == "__main__":   
    main()