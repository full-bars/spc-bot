
function _page_pos(obj) {
    var x, y;
    if (obj.offsetLeft !== undefined) {
        x = obj.offsetLeft;
        y = obj.offsetTop;
    }
    else {
        x = obj.left;
        y = obj.top;
    }

    if (obj.offsetParent !== null) {
        offset_parent = _page_pos(obj.offsetParent);
        x += offset_parent.x;
        y += offset_parent.y;
    }
    return {'x':x, 'y':y};
}

function format_vector(wdir, wspd, units) {
    if (isNaN(wdir) || isNaN(wspd)) {
        return "--";
    }

    if (units === undefined) {
        units = '';
    }
    else {
        units = ' ' + units;
    }

    return wdir.toFixed(0).padStart(3, '0') + '/' + Math.min(99, wspd).toFixed(0).padStart(2, '0') + units;
}

function compare_dt(dt1, dt2) {
    return dt1.isBefore(dt2) ? -1 : (dt1.isSame(dt2) ? 0 : 1);
}

function compute_boundary_segment(bbox, [bdy_u, bdy_v]) {
    const [lbu, lbv, ubu, ubv] = [bbox.lbx, bbox.lby, bbox.ubx, bbox.uby];

    const [bdy_dir, bdy_mag] = comp2vec(bdy_u, bdy_v);
    const bdy_u_dot = -bdy_v / bdy_mag;
    const bdy_v_dot = bdy_u / bdy_mag;
    let alpha_lb, alpha_ub;
    let degenerate = false;

    if (Math.abs(bdy_u_dot) == 0) {
        if (Math.abs(bdy_v_dot) == 0) {
            degenerate = true;
        }
        else {
            alpha_lb = (lbv - bdy_v) / bdy_v_dot;
            alpha_ub = (ubv - bdy_v) / bdy_v_dot;
        }
    }
    else if (Math.abs(bdy_v_dot) == 0) {
        alpha_lb = (lbu - bdy_u) / bdy_u_dot;
        alpha_ub = (ubu - bdy_u) / bdy_u_dot;
    }
    else {
        let alu_lb = (lbu - bdy_u) / bdy_u_dot;
        let alv_lb = (lbv - bdy_v) / bdy_v_dot;
        let alu_ub = (ubu - bdy_u) / bdy_u_dot;
        let alv_ub = (ubv - bdy_v) / bdy_v_dot;

        if (alu_lb > alu_ub) [alu_lb, alu_ub] = [alu_ub, alu_lb];
        if (alv_lb > alv_ub) [alv_lb, alv_ub] = [alv_ub, alv_lb];

        alpha_lb = Math.max(alu_lb, alv_lb);
        alpha_ub = Math.min(alu_ub, alv_ub);
    }

    let bdy_lbu, bdy_ubu, bdy_lbv, bdy_ubv;

    if (degenerate) {
        bdy_lbu = 0;
        bdy_ubu = 0;
        bdy_lbv = lbv;
        bdy_ubv = ubv;
    }
    else {
        bdy_lbu = bdy_u + alpha_lb * bdy_u_dot;
        bdy_ubu = bdy_u + alpha_ub * bdy_u_dot;
        bdy_lbv = bdy_v + alpha_lb * bdy_v_dot;
        bdy_ubv = bdy_v + alpha_ub * bdy_v_dot;
    }

    return [[bdy_lbu, bdy_lbv], [bdy_ubu, bdy_ubv]];
}

function get_media() {
    return window.getComputedStyle(document.querySelector('#screenstate'), ':before').getPropertyValue('content').replace(/['"]/g, '');
}
