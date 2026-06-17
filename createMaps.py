import csv
from datetime import datetime
import json
import os
import tempfile
from pathlib import Path
import folium

from odf.opendocument import FontFaceDecls, OpenDocumentText
from odf.table import Table, TableRow, TableCell, TableColumn
from odf.text import P
from odf.namespaces import TABLENS
from odf.style import FontFace, Footer, Header, HeaderFooterProperties, HeaderStyle, MasterPage, Style, TableColumnProperties, TableCellProperties, ParagraphProperties, TableProperties, TableRowProperties, TextProperties
from odf.style import PageLayout, PageLayoutProperties

COLOR_LIST = [
    'blue', 'green', 'red', 'purple', 'orange', 'darkred', 'lightred',
    'beige', 'darkblue', 'darkgreen', 'cadetblue', 'darkpurple', 'pink',
    'lightblue', 'lightgreen', 'gray', 'black', 'lightgray'
]

base_dir = Path(__file__).resolve().parent

def resolve_linked_data_path(base_name: str) -> Path:
    base_path = base_dir / base_name
    if base_path.exists():
        return base_path

    shortcut_path = base_dir / f"{base_name}.lnk"
    if not shortcut_path.exists():
        raise FileNotFoundError(base_path)

    raw = shortcut_path.read_bytes()
    text = ''.join(chr(b) if 32 <= b < 127 else '\n' for b in raw)
    for line in text.splitlines():
        candidate = line.strip().strip('"')
        if candidate.lower().endswith(Path(base_name).suffix.lower()):
            resolved = Path(candidate)
            if resolved.exists():
                return resolved
            if not resolved.is_absolute() and (base_dir / resolved).exists():
                return base_dir / resolved

    raise FileNotFoundError(f"Verknüpfung '{shortcut_path.name}' enthält keinen gültigen Pfad für '{base_name}'.")


def normalize(value: str) -> str:
    return ' '.join(value.strip().casefold().split())


def _group_sort_key(group_name: str):
    if group_name == 'Alle':
        return (1, group_name)
    try:
        return (0, int(group_name))
    except ValueError:
        return (0, group_name)


def _alt_odt_save_path(odt_file: Path) -> Path:
    return odt_file.with_name(f"{odt_file.stem}_neu{odt_file.suffix}")


def create_maps(locations, entries):
    """Create Folium maps grouped by Gender + Liga with custom legends and layers."""
    location_by_name = {}
    for item in locations:
        name = item.get('name', '').strip()
        if not name:
            continue
        location_by_name[normalize(name)] = item

    # Gruppieren nach Gender + Liga
    maps_data = {}
    for entry in entries:
        gender = entry['Gender']
        liga = entry['Liga']
        if not gender or not liga:
            continue
        key = (gender, liga)
        maps_data.setdefault(key, []).append(entry)

    if not maps_data:
        raise SystemExit('Keine gültigen Gender-Liga-Kombinationen gefunden.')

    summary_entries = []

    for (gender, liga), rows in maps_data.items():
        map_name = f"{gender}_{liga}".replace(' ', '_').replace('/', '-')
        map_file = base_dir / f"{map_name}.html"

        gruppe_values = sorted({row['Gruppe'] or 'Alle' for row in rows})
        layer_groups = {}
        for index, gruppe in enumerate(gruppe_values):
            color = COLOR_LIST[index % len(COLOR_LIST)]
            display_name = gruppe if gruppe == 'Alle' else f"Gruppe {gruppe}"
            layer_groups[gruppe] = {
                'feature_group': folium.FeatureGroup(name=display_name, show=True),
                'color': color,
                'raw_name': gruppe
            }

        folium_map = None
        map_center_locations = []
        missing_locations = []
        group_marker_labels = {}
        
        # Unique identifier counter for HTML IDs in JS operations
        marker_id_counter = 0

        for row in rows:
            verein = row['Verein']
            normalized_verein = normalize(verein)
            location = location_by_name.get(normalized_verein)
            if not location:
                missing_locations.append(verein)
                continue

            lat = float(location['latitude'])
            lon = float(location['longitude'])
            map_center_locations.append((lat, lon))

            gruppe = row['Gruppe'] or 'Alle'
            color = layer_groups[gruppe]['color']

            label = verein
            if row['Mannschaft']:
                label = f"{verein} {row['Mannschaft']}"

            # Generate unique ID for this specific marker instance
            m_id = f"marker_{marker_id_counter}"
            marker_id_counter += 1

            # --- NEW FEATURE: Dropdown selector in the popup ---
            popup_lines = [f"<b>{label}</b>"]
            if location.get('adresse'):
                popup_lines.append(location['adresse'])
            
            # Construct dynamic HTML dropdown for shifting groups
            dropdown_html = f"<div style='margin-top:8px;'><b>Gruppe wechseln:</b><br><select onchange='moveMarkerGroup(\"{m_id}\", this.value)' style='margin-top:4px; padding:2px; font-size:11px;'>"
            for g_val in gruppe_values:
                g_disp = g_val if g_val == 'Alle' else f"Gruppe {g_val}"
                selected = "selected" if g_val == gruppe else ""
                dropdown_html += f"<option value='{g_val}' {selected}>{g_disp}</option>"
            dropdown_html += "</select></div>"
            popup_lines.append(dropdown_html)
            popup_text = '<br>'.join(popup_lines)

            if folium_map is None:
                avg_lat = lat
                avg_lon = lon
                folium_map = folium.Map(location=[avg_lat, avg_lon], zoom_start=10)

            # Create a localized group/holder for this specific team's map elements
            # This makes moving them across Layer FeatureGroups highly straightforward in JS
            team_subgroup = folium.FeatureGroup(name=f"sub_{m_id}")
            
            # Add a colored dot (CircleMarker)
            folium.CircleMarker(
                location=[lat, lon],
                radius=6,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.9,
                popup=folium.Popup(popup_text, max_width=250)
            ).add_to(team_subgroup)

            # Add a persistent label next to the marker using DivIcon (always visible)
            label_html = (
                f"<div style='white-space:nowrap; padding:2px 6px; border-radius:4px; background:rgba(255,255,255,0.9);"
                f"; border:1px solid #666; font-size:12px;'>{label}</div>"
            )
            folium.map.Marker(
                location=[lat, lon],
                icon=folium.DivIcon(html=label_html, icon_size=(150, 20), icon_anchor=(0, -10)),
                popup=folium.Popup(popup_text, max_width=250)
            ).add_to(team_subgroup)

            # Attach this team package onto its starting target feature group
            team_subgroup.add_to(layer_groups[gruppe]['feature_group'])

            display_name = gruppe if gruppe == 'Alle' else f"Gruppe {gruppe}"
            
            # Track metadata for tracking inside JavaScript arrays
            group_marker_labels.setdefault(display_name, []).append({
                'id': m_id,
                'label': label,
                'rawGroup': gruppe
            })

        if missing_locations:
            missing_set = sorted(set(missing_locations))
            print(
                f"Warnung: Für folgende Vereine wurde kein Standort gefunden und sie wurden übersprungen: {', '.join(missing_set)}"
            )

        if not map_center_locations:
            print(f"Warnung: Für '{gender} - {liga}' wurden keine gültigen Standorte gefunden. Karte wird nicht erstellt.")
            summary_entries.append({
                'map_name': map_name,
                'gender': gender,
                'liga': liga,
                'markers': 0,
                'layers': len(layer_groups),
                'missing': len(set(missing_locations)),
                'missing_list': sorted(set(missing_locations)),
                'file_created': False,
            })
            continue

        if folium_map is None:
            avg_lat = sum(lat for lat, _ in map_center_locations) / len(map_center_locations)
            avg_lon = sum(lon for _, lon in map_center_locations) / len(map_center_locations)
            folium_map = folium.Map(location=[avg_lat, avg_lon], zoom_start=10)
        else:
            avg_lat = sum(lat for lat, _ in map_center_locations) / len(map_center_locations)
            avg_lon = sum(lon for _, lon in map_center_locations) / len(map_center_locations)
            folium_map.location = [avg_lat, avg_lon]

        for data in layer_groups.values():
            folium_map.add_child(data['feature_group'])

        folium.LayerControl(collapsed=False).add_to(folium_map)

        # Build legend text and checkbox controls
        legend_items_html = ''

        for gruppe, data in layer_groups.items():
            display_name = gruppe if gruppe == 'Alle' else f"Gruppe {gruppe}"
            group_marker_labels.setdefault(display_name, [])
            color_box = f"<span id='box_{gruppe}' style='display:inline-block;width:14px;height:14px;background:{data['color']};margin-right:8px;border:1px solid #444;vertical-align:middle;'></span>"
            legend_items_html += (
                f"<li style='margin:4px 0;'>"
                f"<label style='cursor:pointer; display:flex; align-items:center;'>"
                f"<input type='checkbox' class='group-toggle' data-group='{display_name}' checked style='margin-right:6px;'/>"
                f"{color_box}{display_name}"
                f"</label>"
                f"<ul class='group-marker-list' data-group='{display_name}' style='list-style:none; padding-left:20px; margin:4px 0 0 0;'></ul>"
                f"</li>"
            )

        legend_html = f"""
        <div id='map_legend' style='position: fixed; bottom: 60px; left: 10px; z-index:9999; background: rgba(255,255,255,0.95); padding:10px; border:1px solid #ccc; border-radius:6px; box-shadow:2px 2px 6px rgba(0,0,0,0.25); max-height:40vh; width:280px; overflow:auto;'>
            <div style='font-weight:700; margin-bottom:6px; font-size:14px;'>{gender} - {liga}</div>
            <ul style='list-style:none; padding:0; margin:0;'>{legend_items_html}</ul>
        </div>
        <style>
        .leaflet-control-layers {{ display: none !important; }}
        .group-marker-list {{ margin:4px 0 0 0; padding-left:16px; }}
        @media (max-height:600px) {{
            #map_legend {{ max-height:50vh; }}
        }}
        </style>
        """

        folium_map.get_root().html.add_child(folium.Element(legend_html))

        # Color mapping dictionary passed safely into JavaScript context
        color_mapping = {g: data['color'] for g, data in layer_groups.items()}
        color_json = json.dumps(color_mapping)
        marker_labels_json = json.dumps(group_marker_labels, ensure_ascii=False)
        
        # --- ENHANCED JAVASCRIPT: Handles map element mutation & legend updates ---
# --- ENHANCED JAVASCRIPT: Handles map element mutation & legend updates ---
        enable_overlays_js = f"""
        <script>
        var groupMarkerLabels = {marker_labels_json};
        var colorMapping = {color_json};
        var fGroups = {{}};
        var subGroups = {{}};

        function refreshGroupMarkerList(groupName, visible) {{
            var list = document.querySelector('ul.group-marker-list[data-group="' + groupName + '"]');
            if (!list) return;
            var dataItems = groupMarkerLabels[groupName] || [];
            list.innerHTML = dataItems.map(function(item) {{
                return '<li style="margin:2px 0; font-size:12px;">' + item.label + '</li>';
            }}).join('');
            list.style.display = visible ? 'block' : 'none';
        }}

        function refreshAllGroupMarkerLists() {{
            Object.keys(groupMarkerLabels).forEach(function(groupName) {{
                var cb = document.querySelector('.group-toggle[data-group="' + groupName + '"]');
                refreshGroupMarkerList(groupName, cb ? cb.checked : true);
            }});
        }}

        // Dynamic Engine for switching marker allocations live
        window.moveMarkerGroup = function(markerId, targetRawGroup) {{
            var targetDisplayName = targetRawGroup === 'Alle' ? 'Alle' : 'Gruppe ' + targetRawGroup;
            var currentRawGroup = null;
            var matchedItem = null;
            
            // Find current structural location of marker metadata
            Object.keys(groupMarkerLabels).forEach(function(gName) {{
                var arr = groupMarkerLabels[gName];
                var foundIdx = arr.findIndex(function(i) {{ return i.id === markerId; }});
                if (foundIdx !== -1) {{
                    matchedItem = arr.splice(foundIdx, 1)[0];
                }}
            }});

            if (!matchedItem) return;

            // Append item into target metadata array
            matchedItem.rawGroup = targetRawGroup;
            if (!groupMarkerLabels[targetDisplayName]) groupMarkerLabels[targetDisplayName] = [];
            groupMarkerLabels[targetDisplayName].push(matchedItem);

            // Handle Leaflet Object Transfer Layer-to-Layer
            var childLayer = subGroups[markerId];
            if (childLayer) {{
                // Remove from all parent groups cleanly
                Object.keys(fGroups).forEach(function(gKey) {{
                    fGroups[gKey].removeLayer(childLayer);
                }});
                
                // Add to target parent FeatureGroup
                if (fGroups[targetRawGroup]) {{
                    fGroups[targetRawGroup].addLayer(childLayer);
                    
                    // Update circle colors within the shifted package to mimic original layer style
                    childLayer.eachLayer(function(layer) {{
                        if (layer.setStyle) {{
                            layer.setStyle({{
                                color: colorMapping[targetRawGroup],
                                fillColor: colorMapping[targetRawGroup]
                            }});
                        }}
                    }});
                }}
            }}

            refreshAllGroupMarkerLists();
        }};

        setTimeout(function() {{
            try {{
                var overlayMap = {{}};
                
                // Track down Folium's global autogenerated Leaflet variable names
                Object.keys(window).forEach(function(key) {{
                    if (key.startsWith('feature_group_')) {{
                        var fg = window[key];
                        if (fg.options && fg.options.name) {{
                            if (fg.options.name.startsWith('sub_marker_')) {{
                                var mId = fg.options.name.replace('sub_', '');
                                subGroups[mId] = fg;
                            }} else {{
                                // Map string names back to parent layout keys
                                var cleanKey = fg.options.name.replace('Gruppe ', '');
                                fGroups[cleanKey] = fg;
                            }}
                        }}
                    }}
                }});

                document.querySelectorAll('.leaflet-control-layers-overlays label').forEach(function(label) {{
                    var input = label.querySelector('input[type=checkbox]');
                    if (!input) return;
                    var text = label.textContent.trim();
                    overlayMap[text] = input;
                }});

                document.querySelectorAll('.group-toggle').forEach(function(cb) {{
                    var target = cb.getAttribute('data-group');
                    cb.addEventListener('change', function() {{
                        var overlayInput = overlayMap[target];
                        if (overlayInput && overlayInput.checked !== cb.checked) {{
                            overlayInput.click();
                        }}
                        refreshGroupMarkerList(target, cb.checked);
                    }});
                }});

                refreshAllGroupMarkerLists();
            }} catch (e) {{ console && console.log && console.log('group legend init error', e); }}
        }}, 1000);

        if (window && window.addEventListener) {{
            window.addEventListener('load', function() {{
                try {{ refreshAllGroupMarkerLists(); }} catch(e){{}}
            }});
        }}
        </script>
        """
        folium_map.get_root().html.add_child(folium.Element(enable_overlays_js))
        folium_map.save(map_file)
        print(f"Die Datei '{map_file.name}' wurde erfolgreich erstellt!")

        summary_entries.append({
            'map_name': map_name,
            'gender': gender,
            'liga': liga,
            'markers': len(map_center_locations),
            'layers': len(layer_groups),
            'missing': len(set(missing_locations)),
            'missing_list': sorted(set(missing_locations)),
            'file_created': True,
        })

    # Zusammenfassungsbericht schreiben
    report_file = base_dir / 'summary_report.txt'
    with report_file.open('w', encoding='utf-8') as report:
        report.write('Map Summary Report\n')
        report.write('==================\n')
        report.write(f'Total maps processed: {len(summary_entries)}\n')
        report.write(f'Total locations: {len(locations)}\n')
        report.write(f'Total CSV entries: {len(entries)}\n\n')

        for summary in summary_entries:
            report.write(f"Map: {summary['map_name']}\n")
            report.write(f"  Gender: {summary['gender']}\n")
            report.write(f"  Liga: {summary['liga']}\n")
            report.write(f"  Markers: {summary['markers']}\n")
            report.write(f"  Layers: {summary['layers']}\n")
            report.write(f"  Missing matches: {summary['missing']}\n")
            if summary['missing_list']:
                report.write(f"  Missing Verein names: {', '.join(summary['missing_list'])}\n")
            report.write(f"  File created: {'Yes' if summary['file_created'] else 'No'}\n")
            report.write('\n')

    print(f"Die Datei '{report_file.name}' wurde erfolgreich erstellt!")

def getAufAbstiegChar(lineNr, aufab, max_len):
    auf, relauf, relab, abst = aufab
    # lineNr is 0-based index from your loop (0, 1, 2, ..., max_len - 1)
    
    # 1. Direct Promotion Zone (Top 'auf' rows)
    if lineNr < auf and auf > 0:
        return '▲'  # Black Up-Pointing Triangle
        
    # 2. Promotion Playoff Zone (Next 'relauf' rows)
    elif lineNr < (auf + relauf) and relauf > 0:
        return '△'  # White Up-Pointing Triangle
        
    # 3. Relegation Playoff Zone ('relab' rows immediately ABOVE the direct relegation rows)
    elif (max_len - abst - relab) <= lineNr < (max_len - abst) and relab > 0:
        return '▽'  # White Down-Pointing Triangle
        
    # 4. Direct Relegation Zone (The absolute last 'abst' rows of the table)
    elif lineNr >= (max_len - abst) and abst > 0:
        return '▼'  # Black Down-Pointing Triangle
        
    else:
        return ''

def get_auf_abstieg_text(aufab, max_len):
    auf, relauf, relab, abst = aufab
    lines = []
    
    # 1. Direkter Aufstieg
    if auf > 0:
        if auf == 1:
            lines.append("Aufstieg: Platz 1")
        else:
            lines.append(f"Aufstieg: Plätze 1 bis {auf}")
            
    # 2. Aufstiegsrelegation
    if relauf > 0:
        if relauf == 1:
            lines.append(f"Aufstiegsrelegation: Platz {auf + 1}")
        else:
            lines.append(f"Aufstiegsrelegation: Plätze {auf + 1} bis {auf + relauf}")
            
    # 3. Abstiegsrelegation (berechnet vom Tabellenende)
    if relab > 0:
        start_relab = max_len - abst - relab + 1
        end_relab = max_len - abst
        if relab == 1:
            lines.append(f"Abstiegsrelegation: Platz {start_relab}")
        else:
            lines.append(f"Abstiegsrelegation: Plätze {start_relab} bis {end_relab}")
            
    # 4. Direkter Abstieg (die letzten 'abst' Plätze)
    if abst > 0:
        start_abst = max_len - abst + 1
        if abst == 1:
            lines.append(f"Abstieg: Platz {max_len}")
        elif abst == 2:
            lines.append(f"Abstieg: Plätze {start_abst} und {max_len}")
        else:
            lines.append(f"Abstieg: Plätze {start_abst} bis {max_len}")
            
    # Zeilen mit Komma trennen (oder mit "\n" für Zeilenumbrüche)
    return ", ".join(lines) if lines else "Kein Auf- und kein Abstieg"



def create_odt(entries):
    """Create Open Document Text file from CSV entries with tables for each Gender-Liga combination."""
    # Group entries by Gender + Liga, then by Gruppe
    gl_groups = {}
    for entry in entries:
        gender = entry['Gender']
        liga = entry['Liga']
        gruppe = entry['Gruppe'] or 'Alle'
        key = (gender, liga)
        
        if key not in gl_groups:
            gl_groups[key] = {}
        if gruppe not in gl_groups[key]:
            gl_groups[key][gruppe] = []
        
        gl_groups[key][gruppe].append(entry)

    # Sort group entries by Verein and Mannschaft for stable table output
    for groups_dict in gl_groups.values():
        for gruppe, entries_list in groups_dict.items():
            entries_list.sort(key=lambda e: (e['Verein'], e['Mannschaft']))
    
    # Create ODF document
    doc = OpenDocumentText()

 # 1. Container for font declarations
    font_decls = FontFaceDecls()
    wingdings_font = FontFace(name="Wingdings", fontfamily="Wingdings")
    font_decls.addElement(wingdings_font)

     # Declare Arial globally
    arial_font = FontFace(name="Arial", fontfamily="Arial")
    font_decls.addElement(arial_font)

    if doc.topnode.childNodes:
        doc.topnode.insertBefore(font_decls, doc.topnode.childNodes[0])
    else:
        doc.topnode.addElement(font_decls)

    page_layout = PageLayout(name="MyPageLayout")
    page_layout.addElement(PageLayoutProperties(
        pagewidth="21cm", 
        pageheight="29.7cm", 
        margin="2cm"       
    ))

    header_style = HeaderStyle()
    # fo:margin-bottom steuert hier den Abstand vom Header zum eigentlichen Text/Tabelle
    header_style.addElement(HeaderFooterProperties(marginbottom="1.0cm"))
    
    # Den Header-Style dem Seitenlayout hinzufügen
    page_layout.addElement(header_style)
    
    # Das Layout im Dokument registrieren
    doc.automaticstyles.addElement(page_layout)

# 2. OVERRIDE THE DEFAULT DOCUMENT STYLES FOR THE WHOLE DOCUMENT
    # We define 'Standard' and 'Default' paragraph styles so LibreOffice defaults to Arial 11pt
    
    # Style A: Default
    default_style = Style(name="Default", family="paragraph")
    default_style.addElement(TextProperties(fontname="Arial", fontfamily="Arial", fontsize="11pt"))
    doc.styles.addElement(default_style)

    # Style B: Standard (LibreOffice's primary fallback for body/table content)
    standard_style = Style(name="Standard", family="paragraph")
    standard_style.addElement(TextProperties(fontname="Arial", fontfamily="Arial", fontsize="11pt"))
    doc.styles.addElement(standard_style)

    liga_title_style = Style(name="LigaTitle", family="paragraph")
    
    liga_title_style = Style(name="LigaTitle", family="paragraph")
    
    # 2. Die ParagraphProperties setzen (hält Überschrift und Tabelle zusammen)
    liga_title_style.addElement(ParagraphProperties(
        marginbottom="0.2cm", 
        margintop="0.5cm",
        keepwithnext="always"
    ))
    
    # 3. KORREKTUR: fontweight="bold" statt bold="true" verwenden!
    liga_title_style.addElement(TextProperties(
        fontname="Arial", 
        fontfamily="Arial", 
        fontsize="14pt", 
        fontweight="bold"  # <- Das ist das korrekte ODF-Attribut für fett gedruckten Text
    ))
    doc.automaticstyles.addElement(liga_title_style)

    # Zeilen-Style erstellen, der den Umbruch innerhalb einer Zeile verhindert
    row_style = Style(name="NoBreakRow", family="table-row")
    #row_style.addElement(TableRowProperties(breakinside="avoid"))
    doc.automaticstyles.addElement(row_style)

    lightgreen_cell_style = Style(name="HellgrüneZelle", family="table-cell")
    lightgreen_cell_style.addElement(TableCellProperties(backgroundcolor="#90EE90", border='0.5pt solid #000000', padding='0.1cm'))
    doc.automaticstyles.addElement(lightgreen_cell_style)

    darkgreen_cell_style = Style(name="DunkelgrüneZelle", family="table-cell")
    darkgreen_cell_style.addElement(TableCellProperties(backgroundcolor="#006400", border='0.5pt solid #000000', padding='0.1cm'))
    doc.automaticstyles.addElement(darkgreen_cell_style)

    orange_cell_style = Style(name="OrangeneZelle", family="table-cell")
    orange_cell_style.addElement(TableCellProperties(backgroundcolor="#FFA500", border='0.5pt solid #000000', padding='0.1cm'))
    doc.automaticstyles.addElement(orange_cell_style)

    right_text_style = Style(name='right_text', family='paragraph')
    right_text_style.addElement(ParagraphProperties(textalign='end'))
    doc.automaticstyles.addElement(right_text_style)
    narrow_col_style = Style(name='col_narrow', family='table-column')
    narrow_col_style.addElement(TableColumnProperties(columnwidth='1cm'))
    medium_col_style = Style(name='col_medium', family='table-column')
    medium_col_style.addElement(TableColumnProperties(columnwidth='2cm'))
    wide_col_style = Style(name='col_wide', family='table-column')
    wide_col_style.addElement(TableColumnProperties(columnwidth='4cm'))
    center_text_style = Style(name='center_text', family='paragraph')
    center_text_style.addElement(ParagraphProperties(textalign='center'))
    center_bold_style = Style(name='center_bold', family='paragraph')
    center_bold_style.addElement(ParagraphProperties(textalign='center'))
    center_bold_style.addElement(TextProperties(fontweight='bold'))
    header_text_style = Style(name='header_text', family='paragraph')
    header_text_style.addElement(ParagraphProperties(textalign='start'))
    header_text_style.addElement(TextProperties(fontweight='bold', fontsize='14pt'))
    pageheader_text_style = Style(name='pageheader_text', family='paragraph')
    pageheader_text_style.addElement(ParagraphProperties(textalign='center'))
    pageheader_text_style.addElement(TextProperties(fontweight='bold', fontsize='16pt'))
    doc.automaticstyles.addElement(pageheader_text_style)


# 4. Create your paragraph text style pointing to Wingdings font
    wingdings_text_style = Style(name="WingdingsText", family='paragraph')
    # Explicitly bind both fontname and fontfamily to "Wingdings"
    wingdings_text_style.addElement(TextProperties(fontname="Wingdings", fontfamily="Wingdings", fontsize="12pt"))
    wingdings_text_style.addElement(ParagraphProperties(textalign='center'))
    doc.automaticstyles.addElement(wingdings_text_style)

    # cell border style
    cell_border_style = Style(name='cell_border', family='table-cell')
    cell_border_style.addElement(TableCellProperties(border='0.5pt solid #000000', padding='0.1cm'))
    doc.automaticstyles.addElement(cell_border_style)

    # --- ADD THIS: Combine wingdings text with standard cell borders ---
    wingdings_cell_style = Style(name='wingdings_cell', family='table-cell')
    wingdings_cell_style.addElement(TableCellProperties(border='0.5pt solid #000000', padding='0.1cm'))
    
    doc.automaticstyles.addElement(wingdings_cell_style)   
    doc.automaticstyles.addElement(narrow_col_style)
    doc.automaticstyles.addElement(medium_col_style)
    doc.automaticstyles.addElement(wide_col_style)
    doc.automaticstyles.addElement(center_text_style)
    doc.automaticstyles.addElement(center_bold_style)
    doc.automaticstyles.addElement(header_text_style)
    doc.automaticstyles.addElement(cell_border_style)

    master_page = MasterPage(name="Standard", pagelayoutname="MyPageLayout")

#   Create and add the Header and Footer to the MasterPage
    header = Header()
    header_text = P(text="Vorläufige Klasseneinteilung Saison 2026/2027", stylename=pageheader_text_style)
    header.addElement(header_text)
    master_page.addElement(header)

    footer = Footer()
    footer_text = P(text="Horst Weigelt " + datetime.now().strftime("%d.%m.%Y"), stylename=right_text_style)
    footer.addElement(footer_text)
    master_page.addElement(footer)

    doc.masterstyles.addElement(master_page)

    # Process each Gender-Liga combination (sorted by desired Gender and Liga sequence)
    gender_order = {'Damen': 0, 'Erwachsene': 1, 'Senioren': 2}
    liga_order = {
        'Bezirksoberliga': 0,
        'Bezirksliga': 1,
        'Bezirksklasse': 2,
        'Kreisliga A': 3,
        'Kreisliga B': 4,
        'Kreisklasse': 5,
    }
    vorsaisonliga_order = {
        'LL'    : 0,
        'LK' : 1,                
        'BL'    : 2,
        'BK'    : 3,
        'KLA'    : 4,
        'KLB'    : 5,
        'KLC'    : 6,
        'KK' : 7
    }

#   Direktaufstieg, Relegationsaufstieg, Relegationsabstieg, Direktabstieg
    aufabstiegsregelung = {
        "Damen Bezirksoberliga": (1, 0, 0, 0),
        "Damen Kreisklasse": (1, 0, 0, 0),
        "Erwachsene Bezirksoberliga": (1, 1, 1, 2),
        "Erwachsene Bezirksliga": (1, 1, 1, 2),
        "Erwachsene Bezirksklasse": (1, 1, 0, 2),
        "Erwachsene Kreisliga A": (2, 0, 0, 2),
        "Erwachsene Kreisliga B": (2, 0, 0, 0),
        "Senioren Bezirksklasse": (1, 0, 0, 0)
    }


    # Warn if any Liga values are not in the defined sequence
    unknown_ligas = sorted({l for (g, l) in gl_groups.keys() if l not in liga_order})
    if unknown_ligas:
        print(f"Warnung: Gefundene Ligen existieren nicht: {', '.join(unknown_ligas)}")

    def _gl_sort_key(item):
        (g, l) = item[0]
        return (gender_order.get(g, 99), liga_order.get(l, 999), g, l)

    def getTableCell(text, tableheader):
        if 'neu' in text:
            cell = TableCell(stylename=darkgreen_cell_style)
        elif 'KK' in text and 'Kreisliga B' in tableheader:
            cell = TableCell(stylename=lightgreen_cell_style)
        elif 'KK' not in text and 'Erwachsene Kreisklasse' in tableheader:
            cell = TableCell(stylename=orange_cell_style)
        else:
            cell = TableCell(stylename=cell_border_style)
        return cell


    def _vorsaison_sort_key(value: str):
        vorsaisonValue = 999
        ligakey = value.split(" ")[0]
        for (liga, pos) in vorsaisonliga_order.items():
            if ligakey.startswith(liga):
                vorsaisonValue = 20 * pos
                ligapos = value.split(" ")[-1]
                return vorsaisonValue + int(ligapos) if ligapos.isdigit() else vorsaisonValue
        if (len(value) > 0):    
            print(f"Warnung: Gefundene Vorsaison-Liga existiert nicht: {ligakey}")
        return 999
                                    

    for (gender, liga), groups_dict in sorted(gl_groups.items(), key=_gl_sort_key):
        groups = sorted(groups_dict.keys(), key=_group_sort_key)
        aufab = aufabstiegsregelung.get(gender + ' ' + liga, (0, 0, 0, 0))
        ueberschrift = f"{gender} {liga}"
        
        # Create tables: 2 groups per table (or 1 if odd number)
        for table_idx in range(0, len(groups), 2):
            groups_in_table = groups[table_idx:table_idx+2]
            
            if len(groups_in_table) == 2:
                # 2-group table: 7 columns
                table = Table(name=f"{gender}_{liga}_{table_idx}")

                # Define 7 columns with narrower side columns and wider content columns
                table.addElement(TableColumn(stylename=narrow_col_style))
                table.addElement(TableColumn(stylename=narrow_col_style))
                table.addElement(TableColumn(stylename=wide_col_style))
                table.addElement(TableColumn(stylename=medium_col_style))
                table.addElement(TableColumn(stylename=narrow_col_style))
                table.addElement(TableColumn(stylename=wide_col_style))
                table.addElement(TableColumn(stylename=medium_col_style))

                # Line 1: Gender - Liga in merged header cell across all columns
                row1 = TableRow()
                cell = TableCell(qattributes={(TABLENS,'number-columns-spanned'): '7'}, stylename=cell_border_style)
                cell.addElement(P(text=ueberschrift, stylename=header_text_style))
                row1.addElement(cell)
                table.addElement(row1)

                grp1 = groups_in_table[0]
                grp2 = groups_in_table[1]

                # Line 2: Group labels for the two contained groups
                row2 = TableRow()
                row2.addElement(TableCell(stylename=cell_border_style))
                row2.addElement(TableCell(stylename=cell_border_style))
                cell = TableCell(qattributes={(TABLENS,'number-columns-spanned'): '2'}, stylename=cell_border_style)
                cell.addElement(P(text=f"Gruppe {grp1}", stylename=center_bold_style))
                row2.addElement(cell)
                row2.addElement(TableCell(stylename=cell_border_style))
                cell = TableCell(qattributes={(TABLENS,'number-columns-spanned'): '2'}, stylename=cell_border_style)
                cell.addElement(P(text=f"Gruppe {grp2}", stylename=center_bold_style))
                row2.addElement(cell)
                table.addElement(row2)

                # Real content rows start here: numbered list in col1, col2 empty,
                # col3 = '{Verein} {Mannschaft}', col4 = '{Vorsaison}', col5 empty,
                # col6 = '{Verein} {Mannschaft}' for group2, col7 = '{Vorsaison}' for group2
                grp1 = groups_in_table[0]
                grp2 = groups_in_table[1]
                list1 = sorted(groups_dict.get(grp1, []), key=lambda e: _vorsaison_sort_key(e.get('Vorsaison', '')))
                list2 = sorted(groups_dict.get(grp2, []), key=lambda e: _vorsaison_sort_key(e.get('Vorsaison', '')))
                max_len = max(len(list1), len(list2))

                for i in range(max_len):
                    r = TableRow()
                    # col1: ongoing number
                    c1 = TableCell(stylename=cell_border_style)
                    c1.addElement(P(text=str(i+1)))
                    r.addElement(c1)

                    # col2: Wingdings column
                    # Use the combined border style for the cell container
                    c2 = TableCell(stylename=wingdings_cell_style)
                    celltext = getAufAbstiegChar(i, aufab, max_len)                                         

                    # Pass the text styling explicitly to the P element
                    c2.addElement(P(text=celltext, stylename=wingdings_text_style))
                    r.addElement(c2)

                    # col3: group1 team
                    if i < len(list1):
                        e = list1[i]
                        team = (e.get('Verein', '') + (' ' + e.get('Mannschaft', '') if e.get('Mannschaft') else '')).strip()
                        c3 = TableCell(stylename=cell_border_style)
                        c3.addElement(P(text=team))
                        r.addElement(c3)
                    else:
                        r.addElement(TableCell())

                    # col4: group1 Vorsaison
                    if i < len(list1):
                        e = list1[i]
                        vorsaison_text = e.get('Vorsaison', '').strip() + '.'
                        if len(vorsaison_text) == 1:
                            vorsaison_text = 'neu'
                        c4 = getTableCell(vorsaison_text, ueberschrift)
                        c4.addElement(P(text=vorsaison_text))
                        r.addElement(c4)
                    else:
                        r.addElement(TableCell())

                    # col5: empty
                    r.addElement(TableCell(stylename=cell_border_style))

                    # col6: group2 team
                    if i < len(list2):
                        e2 = list2[i]
                        team2 = (e2.get('Verein', '') + (' ' + e2.get('Mannschaft', '') if e2.get('Mannschaft') else '')).strip()
                        c6 = TableCell(stylename=cell_border_style)
                        c6.addElement(P(text=team2))
                        r.addElement(c6)
                    else:
                        r.addElement(TableCell())

                    # col7: group2 Vorsaison
                    if i < len(list2):
                        e2 = list2[i]
                        vorsaison_text = e2.get('Vorsaison', '').strip() + '.'
                        if len(vorsaison_text) == 1:
                            vorsaison_text = 'neu'
                        c7 = getTableCell(vorsaison_text, ueberschrift)
                        c7.addElement(P(text=vorsaison_text))
                        r.addElement(c7)
                    else:
                        r.addElement(TableCell())

                    table.addElement(r)
            else:
                # 1-group table: 4 columns
                table = Table(name=f"{gender}_{liga}_{table_idx}")

                # Define 4 columns with narrow leading columns and wider team column
                table.addElement(TableColumn(stylename=narrow_col_style))
                table.addElement(TableColumn(stylename=narrow_col_style))
                table.addElement(TableColumn(stylename=wide_col_style))
                table.addElement(TableColumn(stylename=medium_col_style))

                # Line 1: Gender - Liga in merged header cell across all columns
                row1 = TableRow()
                cell = TableCell(qattributes={(TABLENS,'number-columns-spanned'): '4'}, stylename=cell_border_style)
                cell.addElement(P(text=f"{gender} {liga}", stylename=header_text_style))
                row1.addElement(cell)
                table.addElement(row1)

                # Real content rows start on second line for 1-group tables:
                grp = groups_in_table[0]
                list1 = sorted(groups_dict.get(grp, []), key=lambda e: _vorsaison_sort_key(e.get('Vorsaison', '')))
                max_len = len(list1)
                for i, e in enumerate(list1):
                    r = TableRow()
                    # col1: ongoing number
                    c1 = TableCell(stylename=cell_border_style)
                    c1.addElement(P(text=str(i+1)))
                    r.addElement(c1)

                    # col2: empty
                    celltext = getAufAbstiegChar(i, aufab, max_len)                                         
                    c2 = TableCell(stylename=wingdings_cell_style)
                    c2.addElement(P(text=celltext, stylename=wingdings_text_style))
                    r.addElement(c2)

                    # col3: '{Verein} {Mannschaft}'
                    team = (e.get('Verein', '') + (' ' + e.get('Mannschaft', '') if e.get('Mannschaft') else '')).strip()
                    c3 = TableCell(stylename=cell_border_style)
                    c3.addElement(P(text=team))
                    r.addElement(c3)

                    # col4: '{Vorsaison}'
                    vorsaison_text = e.get('Vorsaison', '').strip() + '.'
                    if len(vorsaison_text) == 1:
                        vorsaison_text = 'neu'
                    c4 = getTableCell(vorsaison_text, ueberschrift)
                    c4.addElement(P(text=vorsaison_text))
                    r.addElement(c4)

                    table.addElement(r)

            doc.text.addElement(table)
            regelung_text = get_auf_abstieg_text(aufab, max_len)
        
            # Text direkt unter die Tabelle setzen
            doc.text.addElement(P())
            doc.text.addElement(P(text=regelung_text))            
            doc.text.addElement(P())
            doc.text.addElement(P())
    
    # Save the document safely to avoid locked-file issues
    odt_file = base_dir / 'klasseneinteilung.odt'
    alt_file = _alt_odt_save_path(odt_file)

    lock_file = base_dir / f".~lock.{odt_file.name}#"
    if lock_file.exists():
        print(
            f"Warnung: Sperrdatei '{lock_file.name}' gefunden. Bitte schließen Sie '{odt_file.name}' bevor Sie das Dokument erneut erstellen."
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix='.odt', dir=str(base_dir)) as tmp:
        tmp_name = tmp.name
    try:
        doc.save(tmp_name)
        try:
            os.replace(tmp_name, str(odt_file))
            print(f"Die Datei '{odt_file.name}' wurde erfolgreich erstellt!")
        except PermissionError as perm_err:
            print(
                f"Warnung: '{odt_file.name}' konnte nicht ersetzt werden ({perm_err}). "
                f"Speichere stattdessen als '{alt_file.name}'."
            )
            doc.save(str(alt_file))
            print(f"Die Datei wurde stattdessen unter '{alt_file.name}' gespeichert.")
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


if __name__ == '__main__':
    # Ortsdaten aus locations.json laden
    locations_file = resolve_linked_data_path('locations.json')
    try:
        with locations_file.open('r', encoding='utf-8') as json_file:
            locations = json.load(json_file)
    except FileNotFoundError:
        raise SystemExit(
            f"Die Datei '{locations_file.name}' wurde nicht gefunden. Bitte lege sie in das Projektverzeichnis: {base_dir}"
        )

    if not locations:
        raise SystemExit("'locations.json' enthält keine Ortsdaten. Bitte prüfe den Inhalt der Datei.")

    # Klasseneinteilung aus CSV oder Windows-Verknüpfung lesen
    klasseneinteilung_file = resolve_linked_data_path('klasseneinteilung.csv')

    entries = []
    try:
        with klasseneinteilung_file.open('r', encoding='utf-8') as csv_file:
            reader = csv.DictReader(csv_file, delimiter=';')
            for row in reader:
                verein = row.get('Verein', '').strip()
                if not verein:
                    continue
                entries.append({
                    'Gender': row.get('Gender', '').strip(),
                    'Liga': row.get('Liga', '').strip(),
                    'Gruppe': row.get('Gruppe', '').strip(),
                    'Verein': verein,
                    'Mannschaft': row.get('Mannschaft', '').strip(),
                    'Vorsaison': row.get('Vorsaison', '').strip(),
                })
    except FileNotFoundError:
        raise SystemExit(
            f"Die Datei '{klasseneinteilung_file.name}' wurde nicht gefunden. Bitte lege sie in das Projektverzeichnis: {base_dir}"
        )

    if not entries:
        raise SystemExit("'klasseneinteilung.csv' enthält keine Daten. Bitte prüfe den Inhalt der Datei.")

    # Execute both tasks
    create_maps(locations, entries)
    #create_odt(entries)
