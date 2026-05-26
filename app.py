from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import pyembroidery
import json
import os
import io
import math
import tempfile
import base64
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
import traceback

app = Flask(__name__, static_folder='static', template_folder='.')
CORS(app, origins='*')

# ── Thread color databases ─────────────────────────────────
MADEIRA_COLORS = {
    1000: {"name": "White", "hex": "#FFFFFF"},
    1001: {"name": "Pale Yellow", "hex": "#FFFACD"},
    1002: {"name": "Yellow", "hex": "#FFD700"},
    1003: {"name": "Golden Yellow", "hex": "#FFA500"},
    1004: {"name": "Orange", "hex": "#FF8C00"},
    1005: {"name": "Red Orange", "hex": "#FF4500"},
    1006: {"name": "Red", "hex": "#DC143C"},
    1007: {"name": "Dark Red", "hex": "#8B0000"},
    1008: {"name": "Pink", "hex": "#FF69B4"},
    1009: {"name": "Rose", "hex": "#FF007F"},
    1010: {"name": "Purple", "hex": "#800080"},
    1011: {"name": "Violet", "hex": "#EE82EE"},
    1012: {"name": "Blue", "hex": "#0000FF"},
    1013: {"name": "Royal Blue", "hex": "#4169E1"},
    1014: {"name": "Sky Blue", "hex": "#87CEEB"},
    1015: {"name": "Turquoise", "hex": "#40E0D0"},
    1016: {"name": "Green", "hex": "#008000"},
    1017: {"name": "Lime Green", "hex": "#32CD32"},
    1018: {"name": "Dark Green", "hex": "#006400"},
    1019: {"name": "Olive", "hex": "#808000"},
    1020: {"name": "Brown", "hex": "#8B4513"},
    1021: {"name": "Dark Brown", "hex": "#5C3317"},
    1022: {"name": "Tan", "hex": "#D2B48C"},
    1023: {"name": "Beige", "hex": "#F5F5DC"},
    1024: {"name": "Gray", "hex": "#808080"},
    1025: {"name": "Silver", "hex": "#C0C0C0"},
    1026: {"name": "Black", "hex": "#000000"},
    1027: {"name": "Gold", "hex": "#FFD700"},
    1028: {"name": "Navy", "hex": "#000080"},
    1029: {"name": "Teal", "hex": "#008080"},
}

DEFAULT_COLORS = [
    "#CC0000", "#0000CC", "#006600", "#CCCC00", "#CC6600",
    "#660066", "#009999", "#CC6699", "#663300", "#000066",
    "#006633", "#990000", "#CC3300", "#003399", "#009900",
    "#CC9900", "#660099", "#00CC66", "#CC0066", "#0066CC",
    "#66CC00", "#CC3300", "#9933CC", "#009999", "#996600",
    "#336699", "#993300", "#3366CC", "#66CC00", "#CC6666",
]

def get_color_hex(color_index, thread_color=None):
    if thread_color and hasattr(thread_color, 'color') and thread_color.color:
        c = thread_color.color
        r = (c >> 16) & 0xFF
        g = (c >> 8) & 0xFF
        b = c & 0xFF
        return "#{:02X}{:02X}{:02X}".format(r, g, b)
    return DEFAULT_COLORS[color_index % len(DEFAULT_COLORS)]

def get_thread_name(thread_color, index):
    if thread_color:
        if hasattr(thread_color, 'name') and thread_color.name:
            return thread_color.name
        if hasattr(thread_color, 'catalog_number') and thread_color.catalog_number:
            return f"#{thread_color.catalog_number}"
    return f"Color {index + 1}"


def safe_get_bounds(pattern):
    xs = []
    ys = []

    for stitch in pattern.stitches:
        try:
            x, y = stitch[0], stitch[1]
            xs.append(x)
            ys.append(y)
        except:
            pass

    if not xs or not ys:
        return 0, 0, 0, 0

    return min(xs), min(ys), max(xs), max(ys)




def recalculate_stitches(pattern, max_stitch=40):
    new_stitches = []

    prev_x = None
    prev_y = None

    for stitch in pattern.stitches:
        x, y, cmd = stitch[0], stitch[1], stitch[2]
        cmd_clean = cmd & pyembroidery.COMMAND_MASK

        if prev_x is not None and cmd_clean == pyembroidery.STITCH:
            dx = x - prev_x
            dy = y - prev_y
            dist = (dx ** 2 + dy ** 2) ** 0.5

            if dist > max_stitch:
                parts = int(dist // max_stitch) + 1

                for i in range(1, parts):
                    nx = int(prev_x + (dx * i / parts))
                    ny = int(prev_y + (dy * i / parts))
                    new_stitches.append([nx, ny, cmd])

        new_stitches.append([x, y, cmd])
        prev_x = x
        prev_y = y

    pattern.stitches = new_stitches
    return pattern


# ── PARSE FILE ─────────────────────────────────────────────
@app.route('/api/parse', methods=['POST'])
def parse_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['file']
        ext  = file.filename.rsplit('.', 1)[-1].lower()
        supported = ['dst','pes','jef','vp3','hus','exp','xxx','emb','sew','pat','pcs','phb','phc']

        if ext not in supported:
            return jsonify({'error': f'.{ext} not supported. Supported: {", ".join(supported).upper()}'}), 400

        # Save temp file
        suffix = f'.{ext}'
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            file.save(tmp_path)

        try:
            pattern = pyembroidery.read(tmp_path)
        finally:
            os.unlink(tmp_path)

        if pattern is None:
            return jsonify({'error': 'Could not parse file. File may be corrupted.'}), 400

        # ── Extract data ──────────────────────────────────
        min_x, min_y, max_x, max_y = safe_get_bounds(pattern)

        width_mm  = round((max_x - min_x) / 10, 1)
        height_mm = round((max_y - min_y) / 10, 1)

        # Validate dimensions
        if width_mm > 1000 or width_mm < 0:  width_mm  = 0
        if height_mm > 1000 or height_mm < 0: height_mm = 0

        # Count stitches properly
        stitch_count = 0
        jump_count   = 0
        trim_count   = 0
        color_changes = 0

        for stitch in pattern.stitches:
            cmd = stitch[2] & pyembroidery.COMMAND_MASK
            if cmd == pyembroidery.STITCH:
                stitch_count += 1
            elif cmd == pyembroidery.JUMP:
                jump_count += 1
            elif cmd in (pyembroidery.TRIM, pyembroidery.SEQUIN_EJECT):
                trim_count += 1
            elif cmd == pyembroidery.COLOR_CHANGE:
                color_changes += 1

        # ── Extract color segments ────────────────────────
        colors_data  = []
        current_pts  = []
        color_idx    = 0
        threads      = pattern.threadlist

        for stitch in pattern.stitches:
            x, y, cmd = stitch[0], stitch[1], stitch[2]
            cmd_clean  = cmd & pyembroidery.COMMAND_MASK

            if cmd_clean == pyembroidery.COLOR_CHANGE or cmd_clean == pyembroidery.STOP:
                if current_pts:
                    thread = threads[color_idx] if color_idx < len(threads) else None
                    colors_data.append({
                        'index'  : color_idx,
                        'color'  : get_color_hex(color_idx, thread),
                        'name'   : get_thread_name(thread, color_idx),
                        'points' : current_pts,
                        'count'  : sum(1 for p in current_pts if p[2] == 0),
                        'jumps'  : sum(1 for p in current_pts if p[2] == 1),
                        'trims'  : sum(1 for p in current_pts if p[2] == 2),
                    })
                color_idx += 1
                current_pts = []
                continue

            if cmd_clean == pyembroidery.END:
                break

            pt_type = 0  # stitch
            if cmd_clean == pyembroidery.JUMP:
                pt_type = 1
            elif cmd_clean == pyembroidery.TRIM:
                pt_type = 2

            current_pts.append([round(x, 1), round(y, 1), pt_type])

        # Last segment
        if current_pts:
            thread = threads[color_idx] if color_idx < len(threads) else None
            colors_data.append({
                'index'  : color_idx,
                'color'  : get_color_hex(color_idx, thread),
                'name'   : get_thread_name(thread, color_idx),
                'points' : current_pts,
                'count'  : sum(1 for p in current_pts if p[2] == 0),
                'jumps'  : sum(1 for p in current_pts if p[2] == 1),
                'trims'  : sum(1 for p in current_pts if p[2] == 2),
            })

        # Estimate thread
        thread_meters = round(stitch_count * 0.035, 1)
        time_min      = max(1, round(stitch_count / 800 + trim_count * 0.1))

        return jsonify({
            'success'      : True,
            'filename'     : file.filename,
            'format'       : ext.upper(),
            'filesize'     : file.content_length or 0,
            'stitch_count' : stitch_count,
            'jump_count'   : jump_count,
            'trim_count'   : trim_count,
            'color_count'  : len(colors_data),
            'width_mm'     : width_mm,
            'height_mm'    : height_mm,
            'width_in'     : round(width_mm / 25.4, 2),
            'height_in'    : round(height_mm / 25.4, 2),
            'bounds'       : {'minX': min_x, 'maxX': max_x, 'minY': min_y, 'maxY': max_y},
            'colors'       : colors_data,
            'time_min'     : time_min,
            'thread_m'     : thread_meters,
            'density'      : round(stitch_count / max(1, (width_mm * height_mm / 100)), 1),
        })

    except Exception as e:
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


# ── RESIZE ─────────────────────────────────────────────────
@app.route('/api/resize', methods=['POST'])
def resize_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file'}), 400

        data      = request.form
        new_w_mm  = float(data.get('width_mm', 0))
        new_h_mm  = float(data.get('height_mm', 0))
        out_fmt   = data.get('format', 'dst').lower()
        recalc    = data.get('recalculate', 'true').lower() == 'true'

        file = request.files['file']
        ext  = file.filename.rsplit('.', 1)[-1].lower()

        with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
            tmp_path = tmp.name
            file.save(tmp_path)

        try:
            pattern = pyembroidery.read(tmp_path)
        finally:
            os.unlink(tmp_path)

        if pattern is None:
            return jsonify({'error': 'Could not read file'}), 400

        min_x, min_y, max_x, max_y = safe_get_bounds(pattern)
        orig_w = (max_x - min_x) / 10  # mm
        orig_h = (max_y - min_y) / 10  # mm

        if orig_w <= 0 or orig_h <= 0:
            return jsonify({'error': 'Invalid design dimensions'}), 400

        sx = new_w_mm / orig_w
        sy = new_h_mm / orig_h

        # Scale all stitch coordinates.
        # Note: this resizes the embroidery geometry. It does not auto re-digitize/add stitches.
        new_stitches = []
        for stitch in pattern.stitches:
            x, y, cmd = stitch[0], stitch[1], stitch[2]
            new_stitches.append([int(round(x * sx)), int(round(y * sy)), cmd])

        pattern.stitches = new_stitches

        # Optional stitch recalculation for enlarged designs
        if recalc and (sx > 1.15 or sy > 1.15):
            pattern = recalculate_stitches(pattern)

        # Write output
        out_suffix = f'.{out_fmt}'
        with tempfile.NamedTemporaryFile(suffix=out_suffix, delete=False) as out_tmp:
            out_path = out_tmp.name

        try:
            pyembroidery.write(pattern, out_path)
            with open(out_path, 'rb') as f:
                file_data = f.read()
        finally:
            os.unlink(out_path)

        # Return file
        out_filename = file.filename.rsplit('.', 1)[0] + f'_resized.{out_fmt}'
        return send_file(
            io.BytesIO(file_data),
            as_attachment=True,
            download_name=out_filename,
            mimetype='application/octet-stream'
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── FORMAT CONVERT ─────────────────────────────────────────
@app.route('/api/convert', methods=['POST'])
def convert_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file'}), 400

        out_fmt = request.form.get('format', 'dst').lower()
        file    = request.files['file']
        ext     = file.filename.rsplit('.', 1)[-1].lower()

        with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
            tmp_path = tmp.name
            file.save(tmp_path)

        try:
            pattern = pyembroidery.read(tmp_path)
        finally:
            os.unlink(tmp_path)

        if pattern is None:
            return jsonify({'error': 'Could not read file'}), 400

        out_suffix = f'.{out_fmt}'
        with tempfile.NamedTemporaryFile(suffix=out_suffix, delete=False) as out_tmp:
            out_path = out_tmp.name

        try:
            pyembroidery.write(pattern, out_path)
            with open(out_path, 'rb') as f:
                file_data = f.read()
        finally:
            os.unlink(out_path)

        out_filename = file.filename.rsplit('.', 1)[0] + f'.{out_fmt}'
        return send_file(
            io.BytesIO(file_data),
            as_attachment=True,
            download_name=out_filename,
            mimetype='application/octet-stream'
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── COLOR CHANGE ───────────────────────────────────────────
@app.route('/api/change_colors', methods=['POST'])
def change_colors():
    try:
        body = request.get_json()
        if not body:
            return jsonify({'error': 'No data'}), 400

        colors_map = body.get('colors', {})  # {color_index: hex_color}
        file_b64   = body.get('file_b64', '')
        ext        = body.get('ext', 'dst')
        out_fmt    = body.get('out_format', ext)

        file_data = base64.b64decode(file_b64)

        with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name

        try:
            pattern = pyembroidery.read(tmp_path)
        finally:
            os.unlink(tmp_path)

        if pattern is None:
            return jsonify({'error': 'Could not read file'}), 400

        # Update thread colors
        for idx_str, hex_color in colors_map.items():
            idx = int(idx_str)
            if idx < len(pattern.threadlist):
                hex_clean = hex_color.lstrip('#')
                r = int(hex_clean[0:2], 16)
                g = int(hex_clean[2:4], 16)
                b = int(hex_clean[4:6], 16)
                pattern.threadlist[idx].color = (r << 16) | (g << 8) | b

        # Write output
        with tempfile.NamedTemporaryFile(suffix=f'.{out_fmt}', delete=False) as out_tmp:
            out_path = out_tmp.name

        try:
            pyembroidery.write(pattern, out_path)
            with open(out_path, 'rb') as f:
                result = base64.b64encode(f.read()).decode()
        finally:
            os.unlink(out_path)

        return jsonify({'success': True, 'file_b64': result})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── BULK CONVERT ───────────────────────────────────────────
@app.route('/api/bulk_convert', methods=['POST'])
def bulk_convert():
    try:
        out_fmt = request.form.get('format', 'dst').lower()
        files   = request.files.getlist('files')

        if not files:
            return jsonify({'error': 'No files'}), 400

        import zipfile
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in files:
                ext = file.filename.rsplit('.', 1)[-1].lower()
                with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
                    tmp_path = tmp.name
                    file.save(tmp_path)
                try:
                    pattern = pyembroidery.read(tmp_path)
                finally:
                    os.unlink(tmp_path)

                if pattern is None:
                    continue

                with tempfile.NamedTemporaryFile(suffix=f'.{out_fmt}', delete=False) as out_tmp:
                    out_path = out_tmp.name
                try:
                    pyembroidery.write(pattern, out_path)
                    out_name = file.filename.rsplit('.', 1)[0] + f'.{out_fmt}'
                    zf.write(out_path, out_name)
                finally:
                    os.unlink(out_path)

        zip_buffer.seek(0)
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=f'converted_{out_fmt}.zip',
            mimetype='application/zip'
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── PDF REPORT ─────────────────────────────────────────────
@app.route('/api/pdf', methods=['POST'])
def generate_pdf():
    try:
        body      = request.get_json()
        design    = body.get('design', {})
        preview   = body.get('preview_b64', '')  # canvas screenshot

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=15*mm, rightMargin=15*mm,
                                topMargin=15*mm, bottomMargin=15*mm)

        styles = getSampleStyleSheet()
        story  = []

        # ── Header ──────────────────────────────────────────
        teal = colors.HexColor('#0ea5a0')

        header_style = ParagraphStyle('header', fontSize=20, fontName='Helvetica-Bold',
                                       textColor=teal, spaceAfter=4)
        sub_style    = ParagraphStyle('sub', fontSize=10, textColor=colors.gray, spaceAfter=12)

        story.append(Paragraph('🐼 Panda Embroidery Viewer', header_style))
        story.append(Paragraph(f"Design: {design.get('filename', 'Unknown')} · Generated {__import__('datetime').datetime.now().strftime('%B %d, %Y')}", sub_style))

        # ── Preview image ────────────────────────────────────
        if preview:
            try:
                img_data = base64.b64decode(preview.split(',')[-1])
                img_buf  = io.BytesIO(img_data)
                rl_img   = RLImage(img_buf, width=80*mm, height=80*mm)
                rl_img.hAlign = 'LEFT'
                story.append(rl_img)
                story.append(Spacer(1, 8*mm))
            except:
                pass

        # ── Design info table ────────────────────────────────
        story.append(Paragraph('Design Information', ParagraphStyle('sh', fontSize=12, fontName='Helvetica-Bold', textColor=teal, spaceAfter=6)))

        info_data = [
            ['Property', 'Value'],
            ['Format',        design.get('format', '—')],
            ['Stitch Count',  f"{design.get('stitch_count', 0):,}"],
            ['Thread Colors', str(design.get('color_count', 0))],
            ['Width',         f"{design.get('width_mm', 0)} mm / {design.get('width_in', 0)}\""],
            ['Height',        f"{design.get('height_mm', 0)} mm / {design.get('height_in', 0)}\""],
            ['Jump Stitches', f"{design.get('jump_count', 0):,}"],
            ['Trims',         str(design.get('trim_count', 0))],
            ['Est. Time',     f"{design.get('time_min', 0)} min @ 800 SPM"],
            ['Thread Needed', f"~{design.get('thread_m', 0)} meters"],
            ['Density',       f"{design.get('density', 0)} st/cm²"],
        ]

        t = Table(info_data, colWidths=[60*mm, 100*mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), teal),
            ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
            ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0,0), (-1,-1), 9),
            ('BACKGROUND', (0,1), (-1,-1), colors.white),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0fdfc')]),
            ('GRID',       (0,0), (-1,-1), 0.5, colors.HexColor('#e5e7eb')),
            ('PADDING',    (0,0), (-1,-1), 6),
        ]))
        story.append(t)
        story.append(Spacer(1, 8*mm))

        # ── Hoop compatibility ───────────────────────────────
        story.append(Paragraph('Hoop Compatibility', ParagraphStyle('sh2', fontSize=12, fontName='Helvetica-Bold', textColor=teal, spaceAfter=6)))

        hoops = [
            ('4"×4"', 97, 97), ('5"×5"', 124, 124), ('5"×7"', 127, 177),
            ('6"×10"', 149, 251), ('8"×8"', 197, 197), ('8"×12"', 197, 302),
            ('9.5"×9.5"', 237, 237),
        ]
        w = design.get('width_mm', 0)
        h = design.get('height_mm', 0)

        hoop_data = [['Hoop', 'Usable Area', 'Status', 'Free Space']]
        for hname, hw, hh in hoops:
            fits = w > 0 and (w + 20 <= hw) and (h + 20 <= hh)
            status = '✓ Fits' if fits else '✗ Too small'
            free   = f"{hw-w:.0f}×{hh-h:.0f}mm" if fits else f"Need {max(0,w+20-hw):.0f}×{max(0,h+20-hh):.0f}mm more"
            hoop_data.append([hname, f'{hw}×{hh}mm', status, free])

        ht = Table(hoop_data, colWidths=[35*mm, 40*mm, 35*mm, 50*mm])
        ht.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), teal),
            ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
            ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0,0), (-1,-1), 9),
            ('GRID',       (0,0), (-1,-1), 0.5, colors.HexColor('#e5e7eb')),
            ('PADDING',    (0,0), (-1,-1), 5),
        ]))
        story.append(ht)
        story.append(Spacer(1, 8*mm))

        # ── Color sequence ───────────────────────────────────
        story.append(Paragraph('Thread Color Sequence', ParagraphStyle('sh3', fontSize=12, fontName='Helvetica-Bold', textColor=teal, spaceAfter=6)))

        color_data = [['#', 'Color', 'Name', 'Stitches', 'Jumps', 'Trims']]
        for c in design.get('colors', []):
            color_data.append([
                str(c.get('index', 0) + 1),
                c.get('color', '#000'),
                c.get('name', f"Color {c.get('index',0)+1}"),
                f"{c.get('count', 0):,}",
                str(c.get('jumps', 0)),
                str(c.get('trims', 0)),
            ])

        ct = Table(color_data, colWidths=[12*mm, 20*mm, 60*mm, 28*mm, 20*mm, 20*mm])
        ct_style = [
            ('BACKGROUND', (0,0), (-1,0), teal),
            ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
            ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0,0), (-1,-1), 9),
            ('GRID',       (0,0), (-1,-1), 0.5, colors.HexColor('#e5e7eb')),
            ('PADDING',    (0,0), (-1,-1), 5),
        ]
        for i, c in enumerate(design.get('colors', []), 1):
            try:
                hex_c = c.get('color', '#cccccc').lstrip('#')
                r,g,b = int(hex_c[0:2],16)/255, int(hex_c[2:4],16)/255, int(hex_c[4:6],16)/255
                ct_style.append(('BACKGROUND', (1,i), (1,i), colors.Color(r,g,b)))
            except:
                pass

        ct.setStyle(TableStyle(ct_style))
        story.append(ct)

        # ── Footer ───────────────────────────────────────────
        story.append(Spacer(1, 10*mm))
        story.append(Paragraph(
            '🐼 Panda Embroidery Viewer · pandadesigns.com · Professional Embroidery Tool Suite',
            ParagraphStyle('footer', fontSize=8, textColor=colors.gray, alignment=TA_CENTER)
        ))

        doc.build(story)
        buf.seek(0)

        return send_file(
            buf,
            as_attachment=True,
            download_name=f"{design.get('filename','design')}_report.pdf",
            mimetype='application/pdf'
        )

    except Exception as e:
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


# ── HEALTH CHECK ───────────────────────────────────────────
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        'status'   : 'ok',
        'service'  : 'Panda Embroidery API',
        'version'  : '1.0.0',
        'supported': ['DST','PES','JEF','VP3','HUS','EXP','XXX','EMB'],
        'features' : ['parse','resize','convert','bulk_convert','change_colors','pdf']
    })

# ── SERVE VIEWER ────────────────────────────────────────────
@app.route('/', methods=['GET'])
def serve_viewer():
    from flask import send_from_directory
    return send_from_directory('.', 'viewer.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
