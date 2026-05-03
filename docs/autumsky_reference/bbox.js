
class BBox {
    constructor(lbx, lby, ubx, uby) {
        this.lbx = lbx;
        this.lby = lby;
        this.ubx = ubx;
        this.uby = uby;
    }

    contains(x, y) {
        return (this.lbx <= x && x <= this.ubx && this.lby <= y && y <= this.uby);
    }

    translate(x, y) {
        return new BBox(this.lbx + x, this.lby + y, this.ubx + x, this.uby + y);
    }

    static union(bbox1, bbox2) {
        var lbx = Math.min(...[bbox1.lbx, bbox2.lbx].filter(e => e !== undefined));
        var lby = Math.min(...[bbox1.lby, bbox2.lby].filter(e => e !== undefined));
        var ubx = Math.max(...[bbox1.ubx, bbox2.ubx].filter(e => e !== undefined));
        var uby = Math.max(...[bbox1.uby, bbox2.uby].filter(e => e !== undefined));

        if (!isFinite(lbx) || !isFinite(lby) || !isFinite(ubx) || !isFinite(uby)) {
              [lbx, lby, ubx, uby] = [undefined, undefined, undefined, undefined];
        }

        return new BBox(lbx, lby, ubx, uby);
    }
}
