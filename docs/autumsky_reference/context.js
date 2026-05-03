
class Context2DWrapper {
    constructor(bbox_pixels, bbox_data, dpr) {
        this.bbox_pixels = bbox_pixels;
        this.bbox_data = bbox_data;
        this.dpr = dpr;

        this._font_size = 10 * this.dpr;
        this._font_face = 'sans-serif';
        this._font_weight = 'normal';
    }

    get(target, prop) {
        if (prop in this) {
            // Override functions that are defined in the wrapper
            if (typeof this[prop] == 'function') {
                return (function() {
                    // Modify the argument list to add the original drawing context as the first argument
                    //  before passing to the wrapper function
                    var args = Array.from(arguments);
                    args.unshift(target);
                    return this[prop].apply(this, args);
                }).bind(this);
            }
            else {
                return this[prop];
            }
        }
        else {
            // Otherwise pass through to the original drawing context
            if (typeof target[prop] == 'function') {
                return target[prop].bind(target);
            }
            else {
                return target[prop];
            }
        }
    }

    set(target, prop, value) {
        if ('__set_' + prop in this) {
            // Override any property with a __set_ name in the wrapper
            return this['__set_' + prop](target, value);
        }
        else {
            // Otherwise pass through to the original drawing context
            return (target[prop] = value);
        }
    }

    circle(ctx, x_data, y_data, rad, rad_units) {
        // Syntactic sugar since I only ever want to draw full circles
        this.arc(ctx, x_data, y_data, rad, 0, 2 * Math.PI, rad_units);
    }

    arc(ctx, x_data, y_data, rad, arc_start, arc_end, rad_units) {
        if (rad_units === undefined) {
            rad_units = 'data';
        }

        var [x, y] = this.data_to_pix(ctx, x_data, y_data);

        var canvas_rad;

        if (rad_units == 'pixels') {
            canvas_rad = rad * this.dpr;
        }
        else if (rad_units == 'data') {
            var [x_edge, y_edge] = this.data_to_pix(ctx, x_data + rad, y_data);
            canvas_rad = x_edge - x;
        }
        else {
            throw "Unknown units '" + rad_units + "'";
        }

        ctx.arc(x, y, canvas_rad, arc_start, arc_end);
    }

    moveTo(ctx, x_data, y_data) {
        var [x, y] = this.data_to_pix(ctx, x_data, y_data);
        ctx.moveTo(x, y);
    }

    lineTo(ctx, x_data, y_data) {
        var [x, y] = this.data_to_pix(ctx, x_data, y_data);
        ctx.lineTo(x, y);
    }

    rect(ctx, x_data_lb, y_data_lb, data_width, data_height) {
        var [lbx, lby] = this.data_to_pix(ctx, x_data_lb, y_data_lb);
        var [ubx, uby] = this.data_to_pix(ctx, x_data_lb + data_width, y_data_lb + data_height);

        ctx.rect(lbx, lby, ubx - lbx, uby - lby);
    }

    translate(ctx, u_data, v_data) {
        var [x_data, y_data] = this.data_to_pix(ctx, u_data, v_data);
        var [x_org, y_org] = this.data_to_pix(ctx, 0, 0);

        ctx.translate(x_data - x_org, y_data - y_org);
    }

    rotate(ctx, angle) {
        var [x_org, y_org] = this.data_to_pix(ctx, 0, 0);

        ctx.translate(x_org, y_org);
        ctx.rotate(angle);
        ctx.translate(-x_org, -y_org);
    }

    fillText(ctx, str, x_data, y_data) {
        var [x, y] = this.data_to_pix(ctx, x_data, y_data);

        str = str + "";

        const n_lines = (str.match(/\n/g) || []).length + 1
        const line_height = ctx.measureText('M').width * 1.2;

        let y_pos;

        if (ctx.textBaseline == 'top' || ctx.textBaseline == 'hanging') {
            y_pos = y;
        }
        else if (ctx.textBaseline == 'middle') {
            y_pos = y - (n_lines - 1) * line_height / 2;
        }
        else if (ctx.textBaseline == 'bottom' || ctx.textBaseline == 'alphabetic') {
            y_pos = y - (n_lines - 1) * line_height;
        }

        str.split("\n").forEach(line => {
            ctx.fillText(line, x, y_pos);
            y_pos += line_height;
        });

    }

    measureText(ctx, str) {
        let obj = {'width': 0};

        str.split("\n").forEach(line => {
            obj['width'] = Math.max(obj['width'], ctx.measureText(line).width);
        });

        return obj;
    }

    setLineDash(ctx, segments) {
        var segs_dpr = segments.map(seg => (seg * this.dpr));
        ctx.setLineDash(segs_dpr);
    }

    __set_bbox_data(_, bbox_data) {
        return (this.bbox_data = bbox_data);
    }

    __set_lineWidth(ctx, line_width) {
        return (ctx.lineWidth = this.dpr * line_width);
    }

    __set_font(ctx, font) {
        var font_regex = /(?:(.*) )?([.\d]+)px (.*)/g
        var font_matches = [...font.matchAll(font_regex)];

        if (font_matches.length > 0) {
            if (font_matches[0][1] !== undefined) {
                this._font_weight = font_matches[0][1];
            }
            else {
                this._font_weight = 'normal';
            }
            this._font_size = this.dpr * parseFloat(font_matches[0][2]);
            this._font_face = font_matches[0][3];
        }

        return (ctx.font = this._font_weight + " " + this._font_size + "px " + this._font_face);
    }

    __set_fontweight(ctx, font_weight) {
        this._font_weight = font_weight;
        return (ctx.font = this._font_weight + " " + this._font_size + "px " + this._font_face);
    }

    __set_fontface(ctx, font_face) {
        this._font_face = font_face;
        return (ctx.font = this._font_weight + " " + this._font_size + "px " + this._font_face);
    }

    __set_fontsize(ctx, font_size) {
        this._font_size = this.dpr * font_size;
        return (ctx.font = this._font_weight + " " + this._font_size + "px " + this._font_face);
    }

    pixelOffset(_, x_data, y_data, x_offset, y_offset) {
        // Computes values in data coordinates with a given pixel offset from a given value
        // First argument is a dummy because the get proxy sticks the original drawing context in there. Surely there's
        //  a better way to handle that?
        var [x, y] = this.data_to_pix(_, x_data, y_data);
        return this.pix_to_data(_, x + x_offset * this.dpr, y + y_offset * this.dpr);
    }

    data_to_pix(_, x_data, y_data) {
        var x = linear_interp(x_data, this.bbox_data.lbx, this.bbox_data.ubx, this.bbox_pixels.lbx, this.bbox_pixels.ubx);
        var y = linear_interp(y_data, this.bbox_data.lby, this.bbox_data.uby, this.bbox_pixels.uby, this.bbox_pixels.lby);
        return [x * this.dpr, y * this.dpr];
    }

    pix_to_data(_, x, y) {
        var x_data = linear_interp(x / this.dpr, this.bbox_pixels.lbx, this.bbox_pixels.ubx, this.bbox_data.lbx, this.bbox_data.ubx);
        var y_data = linear_interp(y / this.dpr, this.bbox_pixels.uby, this.bbox_pixels.lby, this.bbox_data.lby, this.bbox_data.uby);
        return [x_data, y_data];
    }

    static create_proxy(canvas, bbox_pixels, bbox_data, dpr) {
        // Set up a wrapper for the drawing context that handles transformations and the device pixel ratio
        var ctx = canvas.getContext('2d');
        var ctx_wrapper = new Context2DWrapper(bbox_pixels, bbox_data, dpr);
        return new Proxy(ctx, ctx_wrapper)
    }
}
