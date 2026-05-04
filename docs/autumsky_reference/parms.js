
function comp2vec(u, v) {
    var vmag = Math.hypot(u, v);
    var vdir = 90 - Math.atan2(-v, -u) * 180 / Math.PI;
    if (vdir < 0) { vdir += 360; }
    if (vdir >= 360) { vdir -= 360; }

    return [vdir, vmag];
}

function vec2comp(wdir, wspd) {
    var u = -wspd * Math.sin(wdir * Math.PI / 180);
    var v = -wspd * Math.cos(wdir * Math.PI / 180);

    return [u, v];
}

function linear_interp(x, x1, x2, y1, y2) {
    return (x - x1) / (x2 - x1) * (y2 - y1) + y1;
}

function trapz(y, x, x1, x2) {
    if (x2 < x1) {
        throw "trapz: x1 must be less than x2";
    }

    if (x1 < x[0] || x2 > x[x.length - 1]) {
        throw "trapz: integration domain outside data";
    }

    var integ = 0;
    var do_integ = false, do_integ_next = false;
    var last_x = null, last_y = null;

    for (var i = 1; i < x.length; i++) {
        if (x[i - 1] <= x1 && x1 < x[i]) {
            var y1 = linear_interp(x1, x[i - 1], x[i], y[i - 1], y[i]);
            integ += (y1 + y[i]) / 2 * (x[i] - x1);
            do_integ_next = true;
            last_x = x[i];
            last_y = y[i];
        }

        if (x[i - 1] < x2 && x2 <= x[i]) {
            var y2 = linear_interp(x2, x[i - 1], x[i], y[i - 1], y[i]);
            integ += (y2 + last_y) / 2 * (x2 - last_x);
            break;
        }

        if (do_integ) {
            integ += (y[i] + last_y) / 2 * (x[i] - last_x);

            last_x = x[i];
            last_y = y[i];
        }

        if (do_integ_next) {
            do_integ = true;
        }
    }

    return integ;
}

function profile_alt_mean(prof, alt, lyr_lb, lyr_ub) {
    var prof_mean = trapz(prof, alt, lyr_lb, lyr_ub) / (lyr_ub - lyr_lb);
    return prof_mean;

/*
    var prof_sum = 0;
    var pts = 0;

    var do_sum = false;

    for (var i = 1; i < alt.length; i++) {
        if (alt[i - 1] <= lyr_lb && lyr_lb < alt[i]) {
            var v1 = linear_interp(lyr_lb, alt[i - 1], alt[i], prof[i - 1], prof[i]);

            prof_sum += v1;
            pts += 1

            do_sum = true;
        }

        if (alt[i - 1] < lyr_ub && lyr_ub <= alt[i]) {
            var v2 = linear_interp(lyr_ub, alt[i - 1], alt[i], prof[i - 1], prof[i]);

            prof_sum += v2;
            pts += 1

            break;
        }

        if (do_sum) {
            prof_sum += prof[i];
            pts += 1
        }
    }

    return prof_sum / pts;
*/
}

function mean_wind(u, v, alt, lyr_lb, lyr_ub) {
    const u_mean = profile_alt_mean(u, alt, lyr_lb, lyr_ub);
    const v_mean = profile_alt_mean(v, alt, lyr_lb, lyr_ub);

    return [u_mean, v_mean];
}

function wind_shear(u, v, alt, lyr_lb, lyr_ub) {
    var lbu, lbv, ubu, ubv;

    if (lyr_ub < lyr_lb) {
        throw "wind_shear: lower bound must be less than upper bound";
    }

    if (lyr_lb < alt[0] || lyr_ub > alt[alt.length - 1]) {
        throw "wind_shear: layer bounds outside data";
    }

    for (var i = 1; i < alt.length; i++) {
        if (alt[i - 1] <= lyr_lb && lyr_lb < alt[i]) {
            lbu = linear_interp(lyr_lb, alt[i - 1], alt[i], u[i - 1], u[i]);
            lbv = linear_interp(lyr_lb, alt[i - 1], alt[i], v[i - 1], v[i]);
        }
        if (alt[i - 1] < lyr_ub && lyr_ub <= alt[i]) {
            ubu = linear_interp(lyr_ub, alt[i - 1], alt[i], u[i - 1], u[i]);
            ubv = linear_interp(lyr_ub, alt[i - 1], alt[i], v[i - 1], v[i]);
            break;
        }
    }

    return [ubu - lbu, ubv - lbv];
}

function storm_motion(u, v, alt) {
    // Uses the Bunkers (2000) method

    const lyr = 6;
    const dev = 7.5 * 1.94 // Deviation value emperically derived as 7.5 m/s

    let u_shr, v_shr, u_mean, v_mean;

    try {
        [u_mean, v_mean] = mean_wind(u, v, alt, alt[0], lyr);
        const [u_tail, v_tail] = mean_wind(u, v, alt, alt[0], 0.5);
        const [u_tip, v_tip] = mean_wind(u, v, alt, 5.5, 6);

        u_shr = u_tip - u_tail;
        v_shr = v_tip - v_tail;
    }
    catch (err) {
        return {'left': [NaN, NaN], 'right': [NaN, NaN], 'mean': [NaN, NaN]};
    }

    const tmp = dev / Math.hypot(u_shr, v_shr);
    const rstu = u_mean + tmp * v_shr;
    const rstv = v_mean - tmp * u_shr;
    const lstu = u_mean - tmp * v_shr;
    const lstv = v_mean + tmp * u_shr;

    return {'left':[lstu, lstv], 'right':[rstu, rstv], 'mean':[u_mean, v_mean]};
}

function storm_relative_helicity(u, v, alt, lyr_lb, lyr_ub, storm_motions) {
    if (lyr_ub < lyr_lb) {
        throw "storm_relative_helicity: lower bound must be less than upper bound";
    }

    if (lyr_lb < alt[0] || lyr_ub > alt[alt.length - 1]) {
        throw "storm_relative_helicity: layer bounds outside data";
    }

    function compute_srh(u, v, alt, lyr_lb, lyr_ub, ustm, vstm) {
        var sru = u.map(elem => (elem - ustm) / 1.94);
        var srv = v.map(elem => (elem - vstm) / 1.94);

        var integ = 0;
        var do_integ = false, do_integ_next = false;
        var last_sru = null, last_srv = null;

        for (var i = 1; i < alt.length; i++) {
            if (alt[i - 1] <= lyr_lb && lyr_lb < alt[i]) {
                var sru1 = linear_interp(lyr_lb, alt[i - 1], alt[i], sru[i - 1], sru[i]);
                var srv1 = linear_interp(lyr_lb, alt[i - 1], alt[i], srv[i - 1], srv[i]);
                integ += (sru[i] * srv1) - (sru1 * srv[i]);

                do_integ_next = true;
                last_sru = sru[i];
                last_srv = srv[i];
            }

            if (alt[i - 1] < lyr_ub && lyr_ub <= alt[i]) {
                var sru2 = linear_interp(lyr_ub, alt[i - 1], alt[i], sru[i - 1], sru[i]);
                var srv2 = linear_interp(lyr_ub, alt[i - 1], alt[i], srv[i - 1], srv[i]);
                integ += (sru2 * last_srv) - (last_sru * srv2);
                break;
            }

            if (do_integ) {
                integ += (sru[i] * last_srv) - (last_sru * srv[i]);

                last_sru = sru[i];
                last_srv = srv[i];
            }

            if (do_integ_next) {
               do_integ = true;
            }
        }

        return integ;
    }

    if (storm_motions === undefined) {
        storm_motions = storm_motion(u, v, alt);
    }

    var srh = {};
    for (smv in storm_motions) {
        var [ustm, vstm] = storm_motions[smv];
        srh[smv] = compute_srh(u, v, alt, lyr_lb, lyr_ub, ustm, vstm);
    }

    return srh;
}

function critical_angle(u, v, alt, storm_motions) {
    var lyr_ub = 0.5;
    var ubu, ubv;

    if (lyr_ub > alt[alt.length] - 1) {
        throw "critical_angle: data does not extend upward to 500 m";
    }
    if (lyr_ub <= alt[0]) {
        throw "critical_angle: data starts above 500 m";
    }

    for (var i = 1; i < alt.length; i++) {
        if (alt[i - 1] < lyr_ub && lyr_ub <= alt[i]) {
            ubu = linear_interp(lyr_ub, alt[i - 1], alt[i], u[i - 1], u[i]);
            ubv = linear_interp(lyr_ub, alt[i - 1], alt[i], v[i - 1], v[i]);
            break;
        }
    }

    function compute_ca(u, v, ustm, vstm, ubu, ubv) {
        var base_u = ustm - u[0];
        var base_v = vstm - v[0];
        var ang_u = ubu - u[0];
        var ang_v = ubv - v[0];

        var len_base = Math.hypot(base_u, base_v);
        var len_ang = Math.hypot(ang_u, ang_v);
        var base_dot_ang = base_u * ang_u + base_v * ang_v;
        return Math.acos(base_dot_ang / (len_base * len_ang)) * 180 / Math.PI;
    }

    if (storm_motions === undefined) {
        storm_motions = storm_motion(u, v, alt);
    }

    var ca = {};
    ca['lyr_ub'] = [ubu, ubv];

    for (smv in storm_motions) {
        var [ustm, vstm] = storm_motions[smv];
        ca[smv] = compute_ca(u, v, ustm, vstm, ubu, ubv);
    }

    return ca;
}
