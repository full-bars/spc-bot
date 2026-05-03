
class HodoPlot {
    constructor() {
        this._default_hodo_bbox_uv = new BBox(-40, -40, 80, 80);

        this._canvas = document.getElementById("hodo");
        this._setup_canvas();

        this.onscreenshot = null;
        this.onhodorefresh = null;

        this._clear_and_draw_background(this._canvas, this._contexts, this._dpr);

        this._move_callback = null;
        this._done_callback = null;
        this.selecting = false;
        this._selection_anim_bg = null;

        this._tap_moved = false;
        this._setup_event_handlers();

        this._mouse_x = null;
        this._mouse_y = null;

        window.addEventListener('resize', () => {
            const bbox = this._contexts['hodo'].bbox_data;

            this._setup_canvas();
            this._setup_event_handlers();

            this._contexts['hodo'].bbox_data = bbox;

            if (this.onhodorefresh !== null) {
                this.onhodorefresh();
            }
        });
    }

    _setup_event_handlers() {
        this._canvas.onmousemove = () => {};
        this._canvas.onmouseup = () => {};
        this._canvas.onmouseout = () => {};

        this._canvas.ontouchstart = () => {};
        this._canvas.ontouchmove = () => {};
        this._canvas.ontouchcancel = () => {};
        this._canvas.ontouchend = () => {};

        if (get_media() == 'desktop') {
            this._canvas.onmousemove = this.mousemove.bind(this);
            this._canvas.onmouseup = this.mouseclick.bind(this);
            this._canvas.onmouseout = this.mouseleave.bind(this);
        }
        else {
            this._canvas.ontouchstart = event => {
                this._tap_moved = false;
                if (this.selecting) {
                    event.preventDefault();
                    this.mousemove(event.changedTouches[0]);
                }
            };
            this._canvas.ontouchmove = event => {
                this._tap_moved = true;
                if (this.selecting) {
                    event.preventDefault();
                    this.mousemove(event.changedTouches[0]);
                }
            };
            this._canvas.ontouchcancel = event => this.mouseclick(event.changedTouches[0]);
            this._canvas.ontouchend = event => this.mouseclick(event.changedTouches[0]);
        }
    }

    _setup_canvas() {
        this._dpr = window.devicePixelRatio || 1;

        let rect = this._canvas.getBoundingClientRect();
        this._canvas.width = rect.width * this._dpr;
        this._canvas.height = rect.height * this._dpr;

        this._scale_fac = rect.width / 670;
        this._dpr *= this._scale_fac;

        this._contexts = {};

        const hodo_bbox_pixels = new BBox(17.28, 17.92, 449.28, 449.92);
        this._contexts['hodo'] = Context2DWrapper.create_proxy(this._canvas, hodo_bbox_pixels, this._default_hodo_bbox_uv, this._dpr);
        this._contexts['hodo_gr'] = Context2DWrapper.create_proxy(this._canvas, hodo_bbox_pixels, this._default_hodo_bbox_uv, this._dpr);

        this._tab_xlb = 455.92;
        this._tab_xub = 658.36;
        this._tab_top = 17.92;
        this._tab_line_spacing = 12;
        this._tab_spacing = 7;

        this._tables = [
            {'rows': 4, 'cols': [1, 1, 1.4, 1], 'row_headers': ['0-500 m', '0-1 km', '0-3 km', '0-6 km'], 'row_header_weight': 1.5,
                'col_headers': ['BWD\n(kts)', 'LNBS\n(kts)', 'SR Flow\n(kts)', 'SRH\n(m\u{00b2}/s\u{00b2})'], 'col_header_weight': 2},
            {'rows': 5, 'cols': [1], 'row_headers': ['Storm Motion (SM):', 'Bunkers Left Mover (LM):', 'Bunkers Right Mover (RM):', 'Mean Wind (MEAN):', 'Deviant Tor Motion (DTM):'], 'row_header_weight': 3},
            {'rows': 1, 'cols': 1, 'row_headers': ['Critical Angle:'], 'row_header_weight': 3}
        ];

        [this._contexts['table'], this._tab_spacer_ys] = this._generate_table_proxies(this._canvas, this._dpr);

        const srwind_bbox_pixels = new BBox(470, 210, 658.36, 449.92);
        const srwind_bbox_data = new BBox(0, 0, 70, 12);
        this._contexts['srwind'] = Context2DWrapper.create_proxy(this._canvas, srwind_bbox_pixels, srwind_bbox_data, this._dpr);
    }

    reset() {
        this.set_bbox(this._default_hodo_bbox_uv);
        this._contexts['hodo_gr'].bbox_data = this._default_hodo_bbox_uv
        this._clear_and_draw_background(this._canvas, this._contexts, this._dpr);
    }

    set_bbox(bbox) {
        if (bbox == null) {
            this.reset();
        }
        else {
            // Warning: this could let the display and ground-relative bboxes get out of sync. This doesn't happen
            //  currently because the calling functions immediately draw a vwp, which updates the ground-relative bbox.
            this._contexts['hodo'].bbox_data = bbox;
        }
    }

    draw_vwp(vwp) {
        if (vwp !== null) {
            var [vwp_smu, vwp_smv] = vwp.sm_vec;
            var hodo_bbox = this._contexts['hodo'].bbox_data;

            if (vwp.origin == 'storm' && isFinite(vwp_smu) && isFinite(vwp_smv)) {
                this._contexts['hodo_gr'].bbox_data = hodo_bbox.translate(vwp_smu, vwp_smv);
            }
            else {
                this._contexts['hodo_gr'].bbox_data = hodo_bbox;
            }

            this._draw_vwp(vwp, this._canvas, this._contexts, this._dpr);
        }
        else {
            this._clear_and_draw_background(this._canvas, this._contexts, this._dpr);
        }

        this._selection_anim_bg = new Image();
        this._selection_anim_bg.src = this._canvas.toDataURL();
        this._selection_anim_bg.onload = this.mousemove.bind(this);
    }

    mousemove(event) {
        let mx, my;
        if (event === undefined || event.pageX === undefined || event.pageY === undefined) {
            [mx, my] = [this._mouse_x, this._mouse_y];
        }
        else {
            const rect = event.target.getBoundingClientRect();
            mx = (event.pageX - rect.left) / this._scale_fac;
            my = (event.pageY - rect.top) / this._scale_fac;
            [this._mouse_x, this._mouse_y] = [mx, my];
        }

        if (this._move_callback === null) {
            if (this._contexts['hodo'].bbox_pixels.contains(mx, my)) {
                this._canvas.style.cursor = "pointer";
            }
            else {
                this._canvas.style.cursor = "default";
            }
        }
        else {
            const [u, v] = this._contexts['hodo_gr'].pix_to_data(mx * this._dpr, my * this._dpr);

            let ctx_raw = this._canvas.getContext('2d');
            ctx_raw.beginPath();
            ctx_raw.rect(0, 0, this._canvas.width, this._canvas.height);
            ctx_raw.fillStyle='#ffffff';
            ctx_raw.fill();

            ctx_raw.drawImage(this._selection_anim_bg, 0, 0);

            if (this._contexts['hodo_gr'].bbox_data.contains(u, v)) {
                hodo.style.cursor = "crosshair";
                const [wdir, wspd] = comp2vec(u, v);
                this._move_callback(wspd, wdir, event.pageX, event.pageY, this._contexts['hodo_gr']);
            }
            else {
                hodo.style.cursor = "default";
                this._move_callback(null, null, null, null, null);
            }
        }
    }

    mouseclick(event) {
        const rect = event.target.getBoundingClientRect();
        const mx = (event.pageX - rect.left) / this._scale_fac;
        const my = (event.pageY - rect.top) / this._scale_fac;

        if (this._move_callback === null) {
            if (this._contexts['hodo'].bbox_pixels.contains(mx, my) && !this._tap_moved) {
                if (this.onscreenshot !== null) {
                    this.onscreenshot()
                }
            }
        }
        else {
            if (this._contexts['hodo'].bbox_pixels.contains(mx, my)) {
                this.selection_finish(mx, my);
            }
        }
    }

    mouseleave(event) {
        if (this._move_callback !== null) {
            this._move_callback(null, null, null, null, null);
        }
    }

    selection_start(move_callback, done_callback) {
        this._move_callback = move_callback;
        this._done_callback = done_callback;
        this.selecting = true;
    }

    selection_finish(mx, my) {
        if (mx !== null && my !== null) {
            var [u, v] = this._contexts['hodo_gr'].pix_to_data(mx * this._dpr, my * this._dpr);
            var [wdir, wspd] = comp2vec(u, v);
        }
        else {
            var [wdir, wspd] = [null, null];
        }
        this.selecting = false;

        this._done_callback(wspd, wdir);

        this._move_callback = null;
        this._done_callback = null;
        this._selection_anim_bg = null;

        this._canvas.style.cursor = "pointer";
    }

    screenshot(vwp, dpr) {
        if (dpr === undefined) {
            dpr = 4;
        }

        var ss_canvas = document.createElement('canvas');
        ss_canvas.width = this._canvas.width * dpr / this._dpr;
        ss_canvas.height = this._canvas.height * dpr / this._dpr;

        var contexts = {};
        for (var ctx_name in this._contexts) {
            var ctx = this._contexts[ctx_name];
            if (ctx_name == 'table') {
                var [ctx_, _] = this._generate_table_proxies(ss_canvas, dpr);
                contexts[ctx_name] = ctx_;
            }
            else {
                contexts[ctx_name] = Context2DWrapper.create_proxy(ss_canvas, ctx.bbox_pixels, ctx.bbox_data, dpr);
            }
        }

        this._draw_vwp(vwp, ss_canvas, contexts, dpr);

        return ss_canvas;
    }

    _draw_hodo_coordinates(ctx, origin_u, origin_v, color, draw_labels) {
        if (draw_labels === undefined) {
            draw_labels = true;
        }

        var [lbu, lbv, ubu, ubv] = [ctx.bbox_data.lbx, ctx.bbox_data.lby, ctx.bbox_data.ubx, ctx.bbox_data.uby];

        var hodo_ring_spacing = 10;

        var max_u = Math.max(Math.abs(lbu - origin_u), Math.abs(ubu - origin_u));
        var max_v = Math.max(Math.abs(lbv - origin_v), Math.abs(ubv - origin_v));
        var max_ring = Math.hypot(max_u, max_v);
        var min_label = Math.ceil((lbu - origin_u) / hodo_ring_spacing) * hodo_ring_spacing;
        var max_label = Math.floor((ubu - origin_u) / hodo_ring_spacing) * hodo_ring_spacing - hodo_ring_spacing;

        ctx.lineWidth = 1;

        ctx.save();
        ctx.beginPath();
        ctx.rect(lbu, lbv, ubu - lbu, ubv - lbv);
        ctx.clip();

        for (var irng = hodo_ring_spacing; irng < max_ring; irng += hodo_ring_spacing) {
            ctx.beginPath();

            ctx.setLineDash([3, 4]);
            ctx.strokeStyle = color;

            ctx.circle(origin_u, origin_v, irng);
            ctx.stroke();
        }

        if (draw_labels) {
            for (var irng = min_label; irng <= max_label; irng += hodo_ring_spacing) {
                ctx.fontsize = 11;
                ctx.fillStyle = color;
                ctx.textBaseline = 'top';

                var label_text = Math.abs(irng) + "";
                if (irng == max_label) {
                    label_text += " kts";
                }

                var [txtu, txtv] = ctx.pixelOffset(origin_u + irng, origin_v, 1, 1);
                ctx.fillText(label_text, txtu, txtv);
            } 
        }

        ctx.restore()

        ctx.beginPath();
        ctx.setLineDash([]);
        ctx.strokeStyle = color;

        if (lbu < origin_u && origin_u < ubu) {
            ctx.moveTo(origin_u, lbv);
            ctx.lineTo(origin_u, ubv);
        }
        if (lbv < origin_v && origin_v < ubv) {
            ctx.moveTo(lbu, origin_v);
            ctx.lineTo(ubu, origin_v);
        }

        ctx.stroke()
    }

    _clear_and_draw_background(canvas, contexts, dpr) {
        var ctx_raw = canvas.getContext('2d');
        ctx_raw.beginPath();
        ctx_raw.rect(0, 0, canvas.width, canvas.height);
        ctx_raw.fillStyle='#ffffff';
        ctx_raw.fill();

       /**********************************
        * Draw hodograph background
        **********************************/
        var ctx = contexts['hodo'];
        var [lbu, lbv, ubu, ubv] = [ctx.bbox_data.lbx, ctx.bbox_data.lby, ctx.bbox_data.ubx, ctx.bbox_data.uby];
        ctx.fontface = 'Trebuchet MS'

        this._draw_hodo_coordinates(ctx, 0, 0, '#999999', true);

        if (this.selecting) {
            var highlight_colors = ['#dfecec', '#afcfcf', '#80b3b3', '#568f8f'];
            highlight_colors.forEach(function(color, ary_idx, ary) {
                var offset = ary.length - ary_idx;
                var [rect_lbu, rect_lbv] = ctx.pixelOffset(lbu, lbv, -offset, offset)
                var [rect_ubu, rect_ubv] = ctx.pixelOffset(ubu, ubv, offset, -offset)

                ctx.beginPath();
                ctx.strokeStyle = color;
                ctx.rect(rect_lbu, rect_lbv, rect_ubu - rect_lbu, rect_ubv - rect_lbv);
                ctx.stroke();
            });
        }

        ctx.beginPath();
        ctx.strokeStyle = '#000000';
        ctx.rect(lbu, lbv, ubu - lbu, ubv - lbv);
        ctx.stroke();

        var [txtu, txtv] = ctx.pixelOffset(ubu, lbv, 0, 2);
        ctx.fillStyle = '#000000';
        ctx.fontsize = 11;
        ctx.textBaseline = 'top';
        ctx.textAlign = 'right';
        ctx.fillText("https://www.autumnsky.us/vad/", txtu, txtv);

       /**********************************
        * Draw table background
        **********************************/
        let tab_ylb = this._tab_top, tab_yub = tab_ylb + this._tab_line_spacing;

        let x_pix = dpr * (this._tab_xlb + this._tab_xub) / 2;
        let y_pix = dpr * (tab_ylb + tab_yub) / 2
        const header_txt = 'Parameters'

        ctx_raw.font = 'bold ' + (10.5 * dpr) + 'px Trebuchet MS';
        ctx_raw.textBaseline = 'middle';
        ctx_raw.textAlign = 'center';
        ctx_raw.fillText(header_txt, x_pix, y_pix);

        contexts['table'].forEach((tab, idx) => {
            ctx_raw.lineWidth = dpr;
            ctx_raw.strokeStyle = '#000000';
            ctx_raw.moveTo(dpr * this._tab_xlb, dpr * this._tab_spacer_ys[idx]);
            ctx_raw.lineTo(dpr * this._tab_xub, dpr * this._tab_spacer_ys[idx]);
            ctx_raw.stroke();

            tab.draw_headers('bold 10.5px Trebuchet MS');
        });

       /**********************************
        * Draw SR wind plot background
        **********************************/
        ctx = contexts['srwind'];
        ctx.fontface = "Trebuchet MS";
        var [lbs, lbz, ubs, ubz] = [ctx.bbox_data.lbx, ctx.bbox_data.lby, ctx.bbox_data.ubx, ctx.bbox_data.uby];

        const peters_supercell_srw_cutoff = 19.44;
        const peters_supercell_srw_depth = 2;

        ctx.save();
        ctx.fillStyle='#e9e9e9';
        ctx.beginPath();
        ctx.rect(peters_supercell_srw_cutoff, 0, ubs - peters_supercell_srw_cutoff, peters_supercell_srw_depth);
        ctx.fill()

        ctx.fillStyle = '#aaaaaa';
        ctx.textBaseline = 'middle';
        ctx.textAlign = 'center';
        ctx.fontsize = 10;
        ctx.fillText('Peters et al. (2020)\nSupercell', (peters_supercell_srw_cutoff + ubs) / 2, peters_supercell_srw_depth / 2);
        ctx.restore();

        const classic_supercell_srw_cutoff = 40;
        const classic_supercell_srw_zlb = 9;
        const classic_supercell_srw_zub = 11;

        ctx.save();
        ctx.fillStyle='#e9e9e9';
        ctx.beginPath();
        ctx.rect(classic_supercell_srw_cutoff, classic_supercell_srw_zlb, ubs - classic_supercell_srw_cutoff, classic_supercell_srw_zub - classic_supercell_srw_zlb);
        ctx.fill()

        ctx.fillStyle = '#aaaaaa';
        ctx.textBaseline = 'middle';
        ctx.textAlign = 'center';
        ctx.fontsize = 10;
        ctx.fillText('Classic\nSupercell', (classic_supercell_srw_cutoff + ubs) / 2, (classic_supercell_srw_zlb + classic_supercell_srw_zub) / 2);
        ctx.restore();

        ctx.save();

        ctx.beginPath();
        ctx.rect(lbs, lbz, ubs - lbs, ubz - lbz);
        ctx.stroke();

        var srwind_z_spacing = 1;
        var srwind_s_spacing = 10

        ctx.beginPath();
        ctx.setLineDash([3, 4]);
        ctx.strokeStyle = '#999999';

        for (var iz = lbz + srwind_z_spacing; iz < ubz; iz += srwind_z_spacing) {
            ctx.moveTo(lbs, iz);
            ctx.lineTo(ubs, iz);
        }

        for (var is = lbs + srwind_s_spacing; is < ubs; is += srwind_s_spacing) {
            ctx.moveTo(is, lbz);
            ctx.lineTo(is, ubz);
        }

        ctx.stroke();

        ctx.fillStyle = '#000000';
        ctx.fontsize = 11;
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        for (var iz = lbz; iz <= ubz; iz += srwind_z_spacing) {
            var [txtu, txtv] = ctx.pixelOffset(lbs, iz, -2, 0);
            ctx.fillText(iz, txtu, txtv);
        }

        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        for (var is = lbs; is <= ubs; is += srwind_s_spacing) {
            var [txtu, txtv] = ctx.pixelOffset(is, lbz, 0, 2);
            var label = is + '';
            if (is == ubs) {
                label = 'kts  ';
            }
            ctx.fillText(label, txtu, txtv);
        }

        ctx.fillStyle = '#000000';
        ctx.fontsize = 11;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'alphabetic';
        var [txtu, txtv] = ctx.pixelOffset((lbs + ubs) / 2, ubz, 0, -5);
        ctx.fillText('SR Wind vs. Height', txtu, txtv);

        ctx.restore();
    }

    _draw_vwp(vwp, canvas, contexts, dpr) {
        var ctx = contexts['hodo'];

       /**********************************
        * Draw background
        **********************************/
        this._clear_and_draw_background(canvas, contexts, dpr);
/*
        if (vwp.origin == 'storm') {
            var [gnd_x, gnd_y] = vwp.sm_vec;
            gnd_x = -gnd_x;
            gnd_y = -gnd_y;

            this._draw_hodo_coordinates(ctx, gnd_x, gnd_y, '#e6e6e6', false);
        }
*/
       /**********************************
        * Draw hodograph
        **********************************/
        ctx.lineWidth = 2;
        ctx.fontsize = 11;

        vwp.draw(contexts['hodo'], contexts['srwind']);

       /**********************************
        * Draw title
        **********************************/
        var title_str = vwp.radar_id + " VWP valid " + moment.utc(vwp.radar_dt).format("DD MMM YYYY HHmm UTC");
        ctx.fillStyle = '#000000';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'alphabetic';

        var [lbu, lbv, ubu, ubv] = [ctx.bbox_data.lbx, ctx.bbox_data.lby, ctx.bbox_data.ubx, ctx.bbox_data.uby];
        var [txtu, txtv] = ctx.pixelOffset((lbu + ubu) / 2, ubv, 0, -5);

        ctx.fontsize = 14;
        ctx.fillText(title_str, txtu, txtv);

       /**********************************
        * Fill in parameter table
        **********************************/
        let ctxs = contexts['table'];

        function format(val, units) {
            if (isNaN(val)) {
                return '--';
            }

            if (units === undefined) {
                units = "";
            }

            return val.toFixed(0) + units
        }

        function format_vector_(val, units) {
            const [dir, spd] = comp2vec.apply(null, val); 
            return format_vector(dir, spd, units);
        }

        let tab_data = [
            [
                [format(Math.hypot.apply(null, vwp.params['bwd_0_500'])), format(vwp.params['xbdy_bwd_0_500']), format(vwp.params['srmean_0_500']), format(vwp.params['srh_0_500'])],
                [format(Math.hypot.apply(null, vwp.params['bwd_0_1000'])), format(vwp.params['xbdy_bwd_0_1000']), format(vwp.params['srmean_0_1000']), format(vwp.params['srh_0_1000'])],
                [format(Math.hypot.apply(null, vwp.params['bwd_0_3000'])), format(vwp.params['xbdy_bwd_0_3000']), format(vwp.params['srmean_0_3000']), format(vwp.params['srh_0_3000'])],
                [format(Math.hypot.apply(null, vwp.params['bwd_0_6000']))],
            ],
            [
                [format_vector_(vwp.sm_vec, 'kts')], 
                [format_vector_(vwp.params['bunkers_left'], 'kts')], 
                [format_vector_(vwp.params['bunkers_right'], 'kts')], 
                [format_vector_(vwp.params['bunkers_mean'], 'kts')],
                [format_vector_(vwp.params['dtm_obs'], 'kts')]
            ],
            [
                [format(vwp.params['ca'], '\u{00b0}')],
            ]
        ];

        ctxs.forEach((tab, idx) => {
            tab.draw_data(tab_data[idx], '10.5px Trebuchet MS');
        });
    }

    _generate_table_proxies(canvas, dpr) {
        let tab_ylb = this._tab_top, tab_yub = tab_ylb + this._tab_line_spacing;

        let tables = [];
        let tab_spacer_ys = [];

        this._tables.forEach(t => {
            tab_spacer_ys.push(tab_yub + this._tab_spacing / 2);

            let n_rows_tot = t['rows'];
            if (t['col_headers'] !== undefined) { n_rows_tot += (t['col_header_weight'] === undefined ? 1 : 2); }

            tab_ylb = tab_yub + this._tab_spacing;
            tab_yub = tab_ylb + n_rows_tot * this._tab_line_spacing

            let bbox_pixels = new BBox(this._tab_xlb, tab_ylb, this._tab_xub, tab_yub);
            let tab = new Table(t['rows'], t['cols'], canvas, bbox_pixels, dpr);
            if (t['row_headers'] !== undefined) {
                tab.set_row_headers(t['row_headers'], t['row_header_weight']);
            }
            if (t['col_headers'] !== undefined) {
                tab.set_col_headers(t['col_headers'], t['col_header_weight']);
            }

            tables.push(tab);
        });

        return [tables, tab_spacer_ys];
    }
}

