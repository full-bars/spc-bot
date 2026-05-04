
class VWP {
    constructor(radar_id, radar_dt, wdir, wspd, alt, rmse) {
        this.radar_id = radar_id;
        this.radar_dt = radar_dt;

        this.wdir = wdir;
        this.wspd = wspd;
        this.alt = alt;
        this.rmse = rmse;

        this.u = [];
        this.v = [];

        for (var i = 0; i < this.wdir.length; i++) {
            var [u, v] = vec2comp(this.wdir[i], this.wspd[i]);

            this.u.push(u);
            this.v.push(v);
        }

        this.sm_vec_str = 'brm';
        this.sm_vec = null;
        this.sfc_wind = null;
        this.origin = 'ground';
        this.boundary = null;

        this._compute_parameters();
    }

    _compute_parameters() {
        this.params = {};

        var u = this.u;
        var v = this.v;
        var alt = this.alt;

        if (this.sfc_wind !== null) {
            var [usfc, vsfc] = this.sfc_wind;
            u = [usfc].concat(u);
            v = [vsfc].concat(v);
            alt = [0.01].concat(alt);
        }

        var storm_motions = storm_motion(u, v, alt);
        for (var smv in storm_motions) {
            var [ustm, vstm] = storm_motions[smv];
            this.params['bunkers_' + smv] = [ustm, vstm]; //comp2vec(ustm, vstm);
        }

        if (this.sm_vec_str == 'brm') {
            this.sm_vec = storm_motions['right'];
        }
        else if (this.sm_vec_str == 'blm') {
            this.sm_vec = storm_motions['left'];
        }
        else if (this.sm_vec_str == 'mean') {
            this.sm_vec = storm_motions['mean'];
        }
        storm_motions['user'] = this.sm_vec;
        var [smu, smv] = this.sm_vec;

        for (var smvec in storm_motions) {
            var [ustm, vstm] = storm_motions[smvec];
            this.params['sr_bunkers_' + smvec] = [ustm - smu, vstm - smv];
        }

        [0.5, 1, 3, 6].forEach(lyr_ub => {
            try {
                var [shr_u, shr_v] = wind_shear(u, v, alt, alt[0], lyr_ub);
            }
            catch (err) {
                var [shr_u, shr_v] = [NaN, NaN];
            }

            this.params['bwd_0_' + (lyr_ub * 1000)] = [shr_u, shr_v];
        });

        [0.5, 1, 3].forEach(lyr_ub => {
            try {
                var srh = storm_relative_helicity(u, v, alt, alt[0], lyr_ub, {'user': this.sm_vec});
            }
            catch (err) {
                var srh = {'user': NaN};
            }

            this.params['srh_0_' + (lyr_ub * 1000)] = srh['user'];
        });

        try {
            var critical_angles = critical_angle(u, v, alt, {'user': this.sm_vec});
        }
        catch (err) {
            var critical_angles = {'user': NaN, 'lyr_ub': [NaN, NaN]};
        }

        this.params['ca'] = critical_angles['user'];
        this.params['ca_lyr_ub'] = critical_angles['lyr_ub'];
        var [ca_lyr_u, ca_lyr_v] = critical_angles['lyr_ub'];
        this.params['sr_ca_lyr_ub'] = [ca_lyr_u - smu, ca_lyr_v - smv];

        this.sru = [];
        this.srv = [];
        this.srwind = [];

        for (var i = 0; i < this.u.length; i++) {
            this.sru.push(this.u[i] - smu);
            this.srv.push(this.v[i] - smv);
            this.srwind.push(Math.hypot(this.sru[i], this.srv[i]));
        }

        this.sr_sfc_u = null;
        this.sr_sfc_v = null;
        this.sr_sfc_wind = null;
        if (this.sfc_wind !== null) {
            var [usfc, vsfc] = this.sfc_wind;
            this.sr_sfc_u = usfc - smu;
            this.sr_sfc_v = vsfc - smv;
            this.sr_sfc_wind = Math.hypot(this.sr_sfc_u, this.sr_sfc_v);
        }

        [[0, 0.5], [0, 1], [0, 2], [0, 3], [4, 6], [9, 11]].forEach(([lyr_lb, lyr_ub]) => {
            let srmean;
            let srwind = this.srwind.slice();
            const lyr_lb_interp = Math.max(lyr_lb, alt[0]);

            if (this.sr_sfc_wind !== null) {
                srwind.unshift(this.sr_sfc_wind);
            }

            if (lyr_lb_interp < lyr_ub && alt[alt.length - 1] >= lyr_ub) {
                srmean = profile_alt_mean(srwind, alt, lyr_lb_interp, lyr_ub);
            }
            else {
                srmean = NaN;
            }

            this.params['srmean_' + (lyr_lb * 1000) + '_' + (lyr_ub * 1000)] = srmean;
        });

        try {
            this.params['mean_0_300'] = mean_wind(u, v, alt, alt[0], 0.3);
        }
        catch (err) {
            this.params['mean_0_300'] = [NaN, NaN];
        }

        try {
            this.params['mean_0_500'] = mean_wind(u, v, alt, alt[0], 0.5);
        }
        catch (err) {
            this.params['mean_0_500'] = [NaN, NaN];
        }

        var [smu, smv] = storm_motions['user'];
        var [mwu, mwv] = this.params['mean_0_500'];
        this.params['dtm_obs'] = [(smu + mwu) / 2, (smv + mwv) / 2];

        var [smu, smv] = storm_motions['right'];
        var [mwu, mwv] = this.params['mean_0_300'];
        this.params['dtm_b2k'] = [(smu + mwu) / 2, (smv + mwv) / 2];

        [0.5, 1, 3].forEach(lyr_ub => {
            if (this.boundary === null) {
                this.params['xbdy_bwd_0_' + (lyr_ub * 1000)] = NaN;
            }
            else {
                const [bdy_u, bdy_v] = this.boundary;
                const [bwd_u, bwd_v] = this.params['bwd_0_' + (lyr_ub * 1000)];

                const [bdy_dir, bdy_mag] = comp2vec(bdy_u, bdy_v);
                this.params['xbdy_bwd_0_' + (lyr_ub * 1000)] = bdy_u / bdy_mag * bwd_u + bdy_v / bdy_mag * bwd_v;
            }
        });
    }

    has_sm_vec() {
        var [smu, smv] = this.sm_vec;
        return !(isNaN(smu) || isNaN(smv));
    }

    get_bbox() {
        var buffer = 0.4

        var u = this.u;
        var v = this.v;

        if (this.origin == 'storm') {
            u = this.sru;
            v = this.srv;
        }

        var min_u = Math.min(...u);
        var min_v = Math.min(...v);
        var max_u = Math.max(...u);
        var max_v = Math.max(...v);

        if (this.sfc_wind !== null) {
            var [sfc_u, sfc_v] = this.sfc_wind;
            min_u = Math.min(min_u, sfc_u);
            max_u = Math.max(max_u, sfc_u);
            min_v = Math.min(min_v, sfc_v);
            max_v = Math.max(max_v, sfc_v);
        }

        min_u = Math.min(min_u, 0);
        max_u = Math.max(max_u, 0);
        min_v = Math.min(min_v, 0);
        max_v = Math.max(max_v, 0);

        if (this.origin == 'storm') {
            var [smu, smv] = this.sm_vec;
            min_u = Math.min(min_u, -smu);
            max_u = Math.max(max_u, -smu);
            min_v = Math.min(min_v, -smv);
            max_v = Math.max(max_v, -smv);
        }

        var ctr_u = (min_u + max_u) / 2;
        var ctr_v = (min_v + max_v) / 2;

        var side = Math.max(60, Math.max(max_u - min_u, max_v - min_v));

        min_u = ctr_u - (1 + buffer) * side / 2;
        min_v = ctr_v - (1 + buffer) * side / 2;
        max_u = ctr_u + (1 + buffer) * side / 2;
        max_v = ctr_v + (1 + buffer) * side / 2;

        if (isNaN(min_u) || isNaN(max_u) || isNaN(min_v) || isNaN(max_v)) {
            [min_u, max_u, min_v, max_v] = [undefined, undefined, undefined, undefined];
        }

        var bbox = new BBox(min_u, min_v, max_u, max_v);
        return bbox;
    }

    draw(hodo_ctx, srwind_ctx) {

        var colors = ['#ff0000', '#00ff00', '#008800', '#993399', '#00ffff'];
        var seg_cutoffs = [3, 6, 9, 12];

       /**********************************
        * Draw error envelopes
        **********************************/
        var ctx = hodo_ctx;

        var [lbu, lbv, ubu, ubv] = [ctx.bbox_data.lbx, ctx.bbox_data.lby, ctx.bbox_data.ubx, ctx.bbox_data.uby];
        var hodo_u = this.u;
        var hodo_v = this.v;
        var [hodo_sfc_u, hodo_sfc_v] = [NaN, NaN];

        if (this.sfc_wind !== null) {
            [hodo_sfc_u, hodo_sfc_v] = this.sfc_wind;
        }

        if (this.origin == 'storm') {
            hodo_u = this.sru;
            hodo_v = this.srv;
            hodo_sfc_u = this.sr_sfc_u;
            hodo_sfc_v = this.sr_sfc_v;
        }

        if (this.alt.length == 0 || hodo_u.length == 0 || hodo_v.length == 0) {
            ctx.fontsize = 36;
            ctx.fillStyle = '#000000';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText("Empty Profile", (lbu + ubu) / 2, (lbv + ubv) / 2);
            return;
        }

        ctx.save()
        ctx.beginPath();
        ctx.rect(lbu, lbv, ubu - lbu, ubv - lbv);
        ctx.clip();

        ctx.globalAlpha = 0.05;

        var iseg = 0;
        for (var i = 0; i < hodo_u.length; i++) {
            while (this.alt[i] > seg_cutoffs[iseg]) {
                iseg++;
            }

            ctx.beginPath();
            ctx.fillStyle = colors[iseg];
            ctx.circle(hodo_u[i], hodo_v[i], this.rmse[i] * Math.sqrt(2));
            ctx.fill()
        }

        ctx.restore();

       /**********************************
        * Draw hodograph
        **********************************/
        if (this.sfc_wind !== null) {
            ctx.beginPath();
            ctx.strokeStyle = colors[0];
            ctx.setLineDash([4, 3]);
            ctx.moveTo(hodo_sfc_u, hodo_sfc_v);
            ctx.lineTo(hodo_u[0], hodo_v[0]);
            ctx.stroke()
        }

        ctx.beginPath();
        ctx.setLineDash([]);
        ctx.moveTo(hodo_u[0], hodo_v[0]);

        iseg = 0;
        ctx.strokeStyle = colors[iseg];

        for (var i = 1; i < hodo_u.length; i++) {
            while (this.alt[i] > seg_cutoffs[iseg]) {
                var u_cutoff = linear_interp(seg_cutoffs[iseg], this.alt[i - 1], this.alt[i], hodo_u[i -  1], hodo_u[i]);
                var v_cutoff = linear_interp(seg_cutoffs[iseg], this.alt[i - 1], this.alt[i], hodo_v[i -  1], hodo_v[i]);

                ctx.lineTo(u_cutoff, v_cutoff);
                ctx.stroke();

                ctx.beginPath();
                ctx.moveTo(u_cutoff, v_cutoff);

                iseg++;
                ctx.strokeStyle = colors[iseg];
            }

            ctx.lineTo(hodo_u[i], hodo_v[i]);
        }
        ctx.stroke();

       /**********************************
        * Draw critical angle markers
        **********************************/
        ctx.save()
        ctx.lineWidth = 1;

        ctx.beginPath();
        ctx.strokeStyle = '#00cccc';

        var [smu, smv] = this.sm_vec;
        if (this.origin == 'storm') {
            [smu, smv] = [0, 0];
        }
        ctx.moveTo(smu, smv);

        var [low_u, low_v] = [hodo_u[0], hodo_v[0]];
        if (this.sfc_wind !== null) {
            low_u = hodo_sfc_u;
            low_v = hodo_sfc_v;
        }

        ctx.lineTo(low_u, low_v);
        ctx.stroke();        

        ctx.beginPath();
        ctx.strokeStyle = '#cc00cc';

        ctx.moveTo(low_u, low_v);

        var [calu, calv] = this.params['ca_lyr_ub'];
        if (this.origin == 'storm') {
            [calu, calv] = this.params['sr_ca_lyr_ub'];
        }
        ctx.lineTo(calu, calv);

        ctx.stroke();
        ctx.restore();

       /**********************************
        * Draw storm motion markers
        **********************************/
        ctx.save();
        ctx.lineWidth = 1;

        var smv_names = {'bunkers_right': 'RM', 'bunkers_left': 'LM', 'bunkers_mean': 'MEAN'};
        if (this.origin == 'storm') {
            var smv_names = {'sr_bunkers_right': 'RM', 'sr_bunkers_left': 'LM', 'sr_bunkers_mean': 'MEAN'};
        }

        var sm_vec_str = {'brm': 'bunkers_right', 'blm': 'bunkers_left', 'mean': 'bunkers_mean'}[this.sm_vec_str];

        Object.keys(smv_names).forEach((function(smv) {
            var marker_rad = 3;
            var off_sign;

            var [mkru, mkrv] = this.params[smv];

            if (!(this.origin == 'storm' && smv.includes(sm_vec_str))) {
                ctx.beginPath();

                if (smv.includes('bunkers_mean')) {
                    ctx.strokeStyle = '#a04000';
                    ctx.fillStyle = '#a04000';

                    // I don't like this as much as I could
                    var [rect_lbu, rect_lbv] = ctx.pixelOffset(mkru, mkrv, -marker_rad, -marker_rad);
                    var [rect_ubu, rect_ubv] = ctx.pixelOffset(mkru, mkrv, marker_rad, marker_rad);
                    ctx.rect(rect_lbu, rect_lbv, rect_ubu - rect_lbu, rect_ubv - rect_lbv);
                }
                else {
                    ctx.strokeStyle = '#000000';
                    ctx.fillStyle = '#000000';
                    ctx.circle(mkru, mkrv, marker_rad, 'pixels');
                }
            
                ctx.stroke();
            }
            else {
                if (smv.includes('bunkers_mean')) {
                    ctx.strokeStyle = '#a04000';
                    ctx.fillStyle = '#a04000';
                }
                else {
                    ctx.strokeStyle = '#000000';
                    ctx.fillStyle = '#000000';
                }
            }

            ctx.textBaseline = 'alphabetic';
            ctx.textAlign = 'right';
            off_sign = -1;

            var [txt_u, txt_v] = ctx.pixelOffset(mkru, mkrv, off_sign * marker_rad / Math.sqrt(2), off_sign * marker_rad / Math.sqrt(2));
            ctx.fillText(smv_names[smv], txt_u, txt_v);
        }).bind(this));

        if (this.sm_vec_str == 'user') {
            var marker_rad = 3;

            var off_sign;
            ctx.strokeStyle = '#000000';
            ctx.fillStyle = '#000000';


            if (this.origin != 'storm') {
                var [mkru, mkrv] = this.sm_vec;
                var [plus_lbu, plus_lbv] = ctx.pixelOffset(mkru, mkrv, -marker_rad, -marker_rad);
                var [plus_ubu, plus_ubv] = ctx.pixelOffset(mkru, mkrv, marker_rad, marker_rad);

                ctx.beginPath();
                ctx.moveTo(plus_lbu, mkrv);
                ctx.lineTo(plus_ubu, mkrv);
                ctx.moveTo(mkru, plus_lbv);
                ctx.lineTo(mkru, plus_ubv);
                ctx.stroke();
            }
            else {
                var [mkru, mkrv] = [0, 0];
            }

            ctx.textBaseline = 'alphabetic';
            ctx.textAlign = 'right';
            off_sign = -1;

            var [txt_u, txt_v] = ctx.pixelOffset(mkru, mkrv, off_sign * marker_rad / Math.sqrt(2), off_sign * marker_rad / Math.sqrt(2));
            ctx.fillText('SM', txt_u, txt_v);
        }

        ctx.restore();

       /**********************************
        * Draw DTM marker
        **********************************/
        ctx.save();
        ctx.lineWidth = 1;

        var marker_rad = 3;

        var off_sign;
        ctx.strokeStyle = '#000000';
        ctx.fillStyle = '#000000';

        var [mkru, mkrv] = this.params['dtm_obs'];
        if (this.origin == 'storm') {
            var [stmu, stmv] = this.sm_vec;
            mkru -= stmu;
            mkrv -= stmv;
        }

        ctx.beginPath();
        var [tri_u, tri_v] = ctx.pixelOffset(mkru, mkrv, marker_rad, -marker_rad / Math.sqrt(3));
        ctx.moveTo(tri_u, tri_v);
        var [tri_u, tri_v] = ctx.pixelOffset(mkru, mkrv, -marker_rad, -marker_rad / Math.sqrt(3));
        ctx.lineTo(tri_u, tri_v);
        var [tri_u, tri_v] = ctx.pixelOffset(mkru, mkrv, 0, 2 * marker_rad / Math.sqrt(3));
        ctx.lineTo(tri_u, tri_v);
        var [tri_u, tri_v] = ctx.pixelOffset(mkru, mkrv, marker_rad, -marker_rad / Math.sqrt(3));
        ctx.lineTo(tri_u, tri_v);
        ctx.stroke();

        ctx.textBaseline = 'alphabetic';
        ctx.textAlign = 'right';
        off_sign = -1;

        var [txt_u, txt_v] = ctx.pixelOffset(mkru, mkrv, off_sign * marker_rad / Math.sqrt(2), off_sign * marker_rad / Math.sqrt(2));
        ctx.fillText('DTM', txt_u, txt_v);

        ctx.restore();

       /**********************************
        * Draw origin (if storm-relative)
        **********************************/
        if (this.origin == 'storm') {

            ctx.save();
            var [mkru, mkrv] = this.sm_vec;
            mkru = -mkru;
            mkrv = -mkrv;

            ctx.lineWidth = 1;
            ctx.strokeStyle = '#dddddd';
            ctx.fillStyle = '#cccccc';
            ctx.textBaseline = 'top';
            ctx.textAlign = 'right';

            ctx.beginPath();
            ctx.moveTo(lbu, mkrv);
            ctx.lineTo(ubu, mkrv);
            ctx.moveTo(mkru, lbv);
            ctx.lineTo(mkru, ubv);
            ctx.stroke();

            var [txt_u, txt_v] = ctx.pixelOffset(mkru, mkrv, -1, 1);
            ctx.fillText('GRND', txt_u, txt_v);

            ctx.restore();
        }

       /**********************************
        * Draw boundary
        **********************************/
        if (this.boundary !== null) {

            let bbox = ctx.bbox_data;
            const [smu, smv] = this.sm_vec;

            if (this.origin == 'storm') {
                bbox = bbox.translate(smu, smv);
            }

            const [bdy_u, bdy_v] = this.boundary;
            const [bdy_dir, bdy_mag] = comp2vec(bdy_u, bdy_v);

            let [[bdy_lbu, bdy_lbv], [bdy_ubu, bdy_ubv]] = compute_boundary_segment(bbox, this.boundary);
            if (this.origin == 'storm') {
                bdy_lbu -= smu;
                bdy_lbv -= smv;
                bdy_ubu -= smu;
                bdy_ubv -= smv;
            }

            const annot_u = bdy_lbu + (bdy_ubu - bdy_lbu) * 0.05;
            const annot_v = bdy_lbv + (bdy_ubv - bdy_lbv) * 0.05;

            ctx.save();

            ctx.strokeStyle = "#4d4d4d";
            ctx.fillStyle = "#4d4d4d";

            ctx.beginPath();
            ctx.moveTo(bdy_lbu, bdy_lbv);
            ctx.lineTo(bdy_ubu, bdy_ubv);
            ctx.stroke();

            ctx.save();
            ctx.translate(annot_u, annot_v);

            let txt_u, txt_v;
            if (bdy_dir > 90 && bdy_dir <= 270) {
                ctx.rotate((bdy_dir + 180) * Math.PI / 180);
                [txt_u, txt_v] = ctx.pixelOffset(0, 0, 0, 1);
                ctx.textBaseline = "top";
                ctx.textAlign = "right";
            }
            else {
                ctx.rotate(bdy_dir * Math.PI / 180);
                [txt_u, txt_v] = ctx.pixelOffset(0, 0, 0, -3);
                ctx.textBaseline = "alphabetic";
                ctx.textAlign = "left";
            }
            ctx.fillText('QLCS: ' + format_vector(bdy_dir, bdy_mag, 'kts'), txt_u, txt_v);
            ctx.restore();

            var [low_u, low_v] = [hodo_u[0], hodo_v[0]];
            if (this.sfc_wind !== null) {
                low_u = hodo_sfc_u;
                low_v = hodo_sfc_v;
            }

            var mkr_val = 3;
            let u3km, v3km;
            for (var i = 1; i < hodo_u.length; i++) {
                if (this.alt[i - 1] < mkr_val && mkr_val <= this.alt[i]) {
                    u3km = linear_interp(mkr_val, this.alt[i - 1], this.alt[i], hodo_u[i - 1], hodo_u[i]);
                    v3km = linear_interp(mkr_val, this.alt[i - 1], this.alt[i], hodo_v[i - 1], hodo_v[i]);
                    break;
                }
            }

            ctx.beginPath();
            ctx.moveTo(low_u, low_v);
            ctx.lineTo(u3km, v3km);
            ctx.stroke();

            ctx.strokeStyle = "#333333";
            ctx.fillStyle = "#333333";

            const vec_pix_mag = 40;
//          const vec_pix_mag = 1;
            const arrow_pix_size = 8;

/*
            ctx.beginPath()
            ctx.circle(annot_u, annot_v, 3, 'pixels');
            ctx.fill();
*/
            [].forEach(lyr => {
                const [bwd_u, bwd_v] = this.params['bwd_0_' + (lyr * 1000)];
                const [bwd_dir, bwd_mag] = comp2vec(bwd_u, bwd_v);
                const [tip_u, tip_v] = ctx.pixelOffset(0, 0, vec_pix_mag / 2, 0);
                const [tail_u, tail_v] = ctx.pixelOffset(0, 0, -vec_pix_mag / 2 + arrow_pix_size / 2 * Math.sqrt(3), 0);
                const [la_u, la_v] = ctx.pixelOffset(tip_u, tip_v, -arrow_pix_size / 2 * Math.sqrt(3), -arrow_pix_size / 2);
                const [ra_u, ra_v] = ctx.pixelOffset(tip_u, tip_v, -arrow_pix_size / 2 * Math.sqrt(3), arrow_pix_size / 2);

                ctx.save();
                ctx.translate(annot_u, annot_v);
                ctx.rotate((bwd_dir + 90) * Math.PI / 180);

                ctx.beginPath();
                ctx.moveTo(tail_u, tail_v);
                ctx.lineTo(tip_u * 0.95, tip_v);
                ctx.stroke();

                ctx.beginPath();
                ctx.moveTo(tip_u, tip_v);
                ctx.lineTo(la_u, la_v);
                ctx.lineTo(ra_u, ra_v);
                ctx.lineTo(tip_u, tip_v);
                ctx.fill();
                ctx.restore();
            });

            ctx.restore();
        }

       /**********************************
        * Draw height markers
        **********************************/
        ctx.save()
        var marker_fudge = 0.5 // This is unnecessary in Safari
        var marker_rad = 6;

        var mkr_val = Math.floor(Math.min(...this.alt)) + 1;

        const draw_height_marker = (umkr, vmkr, mkr_val) => {
            ctx.beginPath();
            ctx.fillStyle = '#000000';
            ctx.circle(umkr, vmkr, marker_rad, 'pixels');
            ctx.fill();

            ctx.fillStyle = '#ffffff';
            ctx.fontsize = 9;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';

            var [txt_u, txt_v] = ctx.pixelOffset(umkr, vmkr, 0, marker_fudge);
            ctx.fillText(mkr_val, txt_u, txt_v);
        };

        if (this.sfc_wind !== null) {
            var [sfcu, sfcv] = this.sfc_wind;
            for (var mv = 1; mv < mkr_val; mv++) {
                var umkr = linear_interp(mv, 0.1, this.alt[0], sfcu, hodo_u[0]);
                var vmkr = linear_interp(mv, 0.1, this.alt[0], sfcv, hodo_v[0]);

                draw_height_marker(umkr, vmkr, mv);
            }
        }

        for (var i = 1; i < hodo_u.length; i++) {
            while (this.alt[i - 1] < mkr_val && mkr_val <= this.alt[i]) {
                var umkr = linear_interp(mkr_val, this.alt[i - 1], this.alt[i], hodo_u[i - 1], hodo_u[i]);
                var vmkr = linear_interp(mkr_val, this.alt[i - 1], this.alt[i], hodo_v[i - 1], hodo_v[i]);

                draw_height_marker(umkr, vmkr, mkr_val);

                mkr_val++;
            }
        }

        ctx.restore()

       /**********************************
        * Draw surface wind source
        **********************************/
        ctx.save();

        var [txtu, txtv] = ctx.pixelOffset(lbu, lbv, 0, 2);
        ctx.fillStyle = '#000000';
        ctx.fontsize = 11;
        ctx.textBaseline = 'top';
        ctx.textAlign = 'left';
        var sfc_wind_src_txt = "Surface Wind: "

        if (this.sfc_wind !== null) {
            var [hodo_sfc_u, hodo_sfc_v] = this.sfc_wind;
            var [hodo_sfc_wdir, hodo_sfc_wspd] = comp2vec(hodo_sfc_u, hodo_sfc_v);
            sfc_wind_src_txt += format_vector(hodo_sfc_wdir, hodo_sfc_wspd) + " (" + this.sfc_wind_src + ")"
        }
        else {
            sfc_wind_src_txt += "None"
        }
        ctx.fillText(sfc_wind_src_txt, txtu, txtv);

        ctx.restore();

       /**********************************
        * Draw SR wind
        **********************************/
        ctx = srwind_ctx;

        var [lbs, lbz, ubs, ubz] = [ctx.bbox_data.lbx, ctx.bbox_data.lby, ctx.bbox_data.ubx, ctx.bbox_data.uby];

        // Should I add the plot bounds as a clip path?
        ctx.save();
        ctx.beginPath();
        ctx.rect(lbs, lbz, ubs - lbs, ubz - lbz);
        ctx.clip();
        var srwind = this.srwind;
        var alt = this.alt;
        ctx.strokeStyle = '#ff0000';

        if (this.sr_sfc_wind !== null) {
            ctx.setLineDash([4, 3]);
            ctx.beginPath();
            ctx.moveTo(this.sr_sfc_wind, 0.01);
            ctx.lineTo(srwind[0], alt[0]);
            ctx.stroke();
        }
        
        ctx.setLineDash([]);
        ctx.linewidth = 2;
        ctx.beginPath();
        ctx.moveTo(srwind[0], alt[0]);
        for (var i = 1; i < srwind.length; i++) {
            ctx.lineTo(srwind[i], alt[i]);
        }
        ctx.stroke();

        ctx.restore();

        ctx.save();
        let color = '#000000'; //'#ff00ff', '#ff0000', '#00ff00'];
        [[0, 2], [4, 6], [9, 11]].forEach(([lyr_lb, lyr_ub]) => {
            const key = 'srmean_' + (lyr_lb * 1000) + '_' + (lyr_ub * 1000);

            ctx.setLineDash([4, 3]);
            ctx.strokeStyle = color;

            ctx.beginPath();
            ctx.moveTo(this.params[key], lyr_lb);
            ctx.lineTo(this.params[key], lyr_ub);
            ctx.stroke()
        });
        ctx.restore();
    }

    change_surface_wind(new_vec, source) {
        if (typeof new_vec == 'string') {
            if (new_vec.toLowerCase() == 'none') {
                this.sfc_wind = null;
            }
        }
        else{
            var [wdir, wspd] = new_vec;
            this.sfc_wind = vec2comp(wdir, wspd);
        }

        this.sfc_wind_src = source;

        if (this.sm_vec_str != 'user') {
            // Force a recompute of the storm motion vector if the user hasn't set one
            this.sm_vec = null;
        }

        this._compute_parameters();
    }

    change_storm_motion(new_vec) {
        if (typeof new_vec == 'string') {
            this.sm_vec_str = new_vec.toLowerCase();
        }
        else {
            var [wdir, wspd] = new_vec;
            this.sm_vec = vec2comp(wdir, wspd);
            this.sm_vec_str = 'user';
        }
        this._compute_parameters();
    }

    change_origin(origin) {
        this.origin = origin;
    }

    change_boundary(new_vec) {
        if (typeof new_vec == 'string' && new_vec.toLowerCase() === 'none') {
            this.boundary = null;
        }
        else {
            let [bdy_dir, bdy_spd] = new_vec;
            this.boundary = vec2comp(bdy_dir, bdy_spd);
        }
        this._compute_parameters();
    }

    static from_server(radar_id, file_id, force, _delay_debug, _fail_debug) {
        if (_delay_debug === undefined) {
            _delay_debug = false;
        }

        if (_fail_debug === undefined) {
            _fail_debug = false;
        }

        var root_url = $('#root_url').val();
        var session_id = $('#session_id').val();

        if (!window.location.hostname.includes('www')) {
            root_url = root_url.replace('www.', '');
        }

        var url = root_url + "/vad/get_radar_json.php?radar=" + radar_id + '&session_id=' + session_id + '&force=' + force;
        if (file_id !== null) {
            url += '&id=' + file_id;
        }

        console.log(url);

        return (new Promise((resolve, reject) => {
            $.getJSON(url).done(resolve).fail(reject);
        })).then(json => {
            if (!_delay_debug) {
                return json;
            }

            return new Promise((resolve, reject) => { settimeout(() => resolve(json), 10000); });
        }).then(json => {
            if (_fail_debug) {
                throw "Some error happened";
            }

            return json;
        }).then(json => {
            json['warnings'].forEach(warn => console.warn(warn));
            return VWP.from_json(json['response']);
        });
    }

    static from_json(json) {
        var vwp = new VWP(json['radar_id'], moment.utc(json['datetime']),
                          json['data']['wind_dir'], json['data']['wind_spd'], json['data']['altitude'], json['data']['rms_error']);
        return vwp;
    }

    static from_blob(blob) {
        const zip = arys => arys[0].map((_, idx) => arys.map(ary => ary[idx]));

        return blob.arrayBuffer().then(buffer => {
            let warning = "";
            let dataview = new DataView_(buffer);

            let header_len = 30;
            const wmo_header = dataview.getUTF8String(header_len);
            const match = wmo_header.match(/.* (\w)\w{3} .*NVW(\w{3})/s);
            if (match === null) {
                throw {'long': "Error parsing VWP: could not parse WMO headers. This is not a VWP data file.", 'short': "Couldn't parse WMO headers"};
            }
            const radar_id = match[1] + match[2];

            try {
                const dataview_dec = dataview.getDecompressedView();
                header_len = 54;
                dataview_dec.getUTF8String(header_len);
                dataview = dataview_dec;
            }
            catch(e) {
            }

            const msg_code = dataview.getInt16();
            const msg_julian_date = dataview.getInt16();
            const msg_secs_midnight = dataview.getInt32();
            const msg_length = dataview.getInt32();
            const source_id = dataview.getInt16();
            const dest_id = dataview.getInt16();
            const num_blocks = dataview.getInt16();

            dataview.getInt16();

            const radar_latitude = dataview.getInt32() / 1000.;
            const radar_longitude = dataview.getInt32() / 1000.;
            const radar_elevation = dataview.getInt16();
            const product_code = dataview.getInt16();

            if (product_code != 48) {
                throw {'long': "Error parsing VWP: unexpected product code (" + product_code + "). This is not a VWP data file.", 'short': "Unexpected product code"};
            }

            dataview.getInt16();
            const vcp = dataview.getInt16();
            dataview.getInt16();
            dataview.getInt16();

            const scan_jul_date = dataview.getInt16();
            const scan_secs_midnight = dataview.getInt32();
            const prod_jul_date = dataview.getInt16();
            const prod_secs_midnight = dataview.getInt32();

            const dt = moment.utc('1969-12-31T00:00:00Z').add(scan_jul_date, 'days').add(scan_secs_midnight, 'seconds');

            dataview.seek(56, DataView_.ANCH_CUR);
            
            const offset_symbology_block = dataview.getInt32();
            const offset_graphic_block = dataview.getInt32();
            const offset_tabular_block = dataview.getInt32();

            function parse_symbology_block(dataview) {
                dataview.seek(offset_symbology_block * 2 + header_len);
                dataview.getInt16()
                let block_id = dataview.getInt16();

                if (block_id != 1) {
                    throw {'long': "Error parsing VWP: unexpected block_id (" + block_id + "). File may be corrupt.", 'short': "Unexpected block_id"};
                }

                const block_len = dataview.getInt32();
                let num_layers = dataview.getInt16();

                const packet_readers = {
                    4: dv => {
                        const packet_len = dataview.getInt16();
                        let ret = {'color': dv.getInt16(), 'i': dv.getInt16(), 'j': dv.getInt16(),
                                   'drct': dv.getInt16(), 'sknt': dv.getInt16()};
                        return [packet_len, ret];
                    },
                    8: dv => {
                        const packet_len = dataview.getInt16();
                        let ret = {'color': dv.getInt16(), 'i': dv.getInt16(), 'j': dv.getInt16(), 
                                   'str': dv.getUTF8String(packet_len - 6)};
                        return [packet_len, ret];
                    },
                    10: dv => {
                        const packet_len = dataview.getInt16();
                        const n_vects = (packet_len - 2) / 8;

                        let ret = {'color': dv.getInt16(), 'istart': [], 'jstart': [], 'iend': [], 'jend': []};
                        for (let ivct = 0; ivct < n_vects; ivct++) {
                            ret['istart'].push(dv.getInt16());
                            ret['jstart'].push(dv.getInt16());
                            ret['iend'].push(dv.getInt16());
                            ret['jend'].push(dv.getInt16());
                        }

                        return [packet_len, ret];
                    }
                }

                let packets = [];

                for (let ilyr = 0; ilyr < num_layers; ilyr++) {
                    dataview.getInt16();
                    const layer_len = dataview.getInt32();
                    let len_all_packets = 0;
                    let early_exit = false;

                    while (len_all_packets < layer_len) {
                        let packet_type, packet_len, packet;
                        try {
                            packet_type = dataview.getInt16();
                            [packet_len, packet] = packet_readers[packet_type](dataview);
                        }
                        catch (e) {
                            warning = "Data may be incomplete";
                            console.warn('Unexpected EOF; data may be incomplete');
                            early_exit = true;
                            break;
                        }
                        len_all_packets += (packet_len + 4);
                        packet['type'] = packet_type;
                        packets.push(packet);
                    }

                    if (early_exit) break;
                }

                const j_ticks = packets[1]['jstart'].reverse();
                const j_alts = packets.filter(p => p['type'] == 8).filter(p => p['i'] == 31).map(p => parseInt(p['str']));
                const alt_map = Object.fromEntries(zip([j_ticks, j_alts]));
                const rms_map = {1: 2, 2: 6, 3: 10, 4: 14, 5: 18};
                const stack_i = packets.filter(p => p['type'] == 8).filter(p => p['str'] == dt.format('HHmm'))[0]['i'];
                let prof_packets = packets.filter(p => p['type'] == 4).filter(p => p['i'] >= stack_i);

                let altitude = [];
                let wind_dir = [];
                let wind_spd = [];
                let rms_error = [];

                prof_packets.forEach(p => {
                    altitude.push((alt_map[p['j']] - radar_elevation / 1000) / 3.281);
                    wind_dir.push(p['drct']);
                    wind_spd.push(p['sknt']);
                    rms_error.push(rms_map[p['color']]);
                });

                return [altitude, wind_dir, wind_spd, rms_error];
            }

            function parse_tabular_block(dataview) {
                dataview.seek(offset_tabular_block * 2 + header_len);

                dataview.getInt16();
                const block_id = dataview.getInt16();

                if (block_id != 3) {
                    throw {'long': "Error parsing VWP: unexpected block_id (" + block_id + "). File may be corrupt.", 'short': "Unexpected block_id"};
                }

                const block_size = dataview.getInt32();
                dataview.seek(120, DataView_.ANCH_CUR);

                let text_msg = [];

                dataview.getInt16();
                const num_pages = dataview.getInt16();
                for (let ipg = 0; ipg < num_pages; ipg++) {
                    let page = [];
                    let num_chars = dataview.getInt16();

                    while (num_chars != -1) {
                        let msg = dataview.getUTF8String(num_chars);
                        page.push(msg);

                        num_chars = dataview.getInt16();
                    }

                    text_msg.push(page);
                }

                const vad_list = text_msg.filter(page => page[0].trim().startsWith('VAD Algorithm Output')).map(page => page.slice(3)).flat();
                const r_e = 4. / 3. * 6371;

                let wind_dir = [];
                let wind_spd = [];
                let rms_error = [];
                let divergence = [];
                let altitude = [];

                vad_list.forEach(line => {
                    const vals = line.trim().split(/\s+/);
                    wind_dir.push(parseFloat(vals[4]));
                    wind_spd.push(parseFloat(vals[5]));
                    rms_error.push(parseFloat(vals[6]));
                    divergence.push(vals[7] == 'NA' ? NaN : parseFloat(vals[7]));

                    const slant_range = parseFloat(vals[8]) * 6067.1 / 3281.;
                    const elev_angle = parseFloat(vals[9]);

                    altitude.push(Math.sqrt(Math.pow(r_e, 2) + Math.pow(slant_range, 2) + 2 * r_e * slant_range * Math.sin(elev_angle * Math.PI / 180)) - r_e);
                });

                return [altitude, wind_dir, wind_spd, rms_error];
            }

            let altitude, wind_dir, wind_spd, rms_error;

            try {
                [altitude, wind_dir, wind_spd, rms_error] = parse_tabular_block(dataview);

                if (altitude.length == 0 || wind_dir.length == 0 || wind_spd.length == 0 || rms_error.length == 0) {
                    throw "";
                }
                console.log('... parsed from the tabular block');
            }
            catch (error) {
                [altitude, wind_dir, wind_spd, rms_error] = parse_symbology_block(dataview);
                console.log('... parsed from the symbology block');
            }

            const keysort = function() {
                return zip(zip([...arguments]).sort((elem1, elem2) => elem1[0] - elem2[0]));
            };

            [altitude, wind_dir, wind_spd, rms_error] = keysort(altitude, wind_dir, wind_spd, rms_error);

            const vwp = new VWP(radar_id, dt, wind_dir, wind_spd, altitude, rms_error);
            return {'warning': warning, 'vwp': vwp};
        });
    }
}
