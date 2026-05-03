
class VWPContainer {
    constructor(age_limit) {
        this._age_limit = age_limit;

        this.onrefreshstart = null;
        this.onrefreshend = null;
        this.onanimationplay = null;
        this.onanimationpause = null;
        this.onnewframelist = null;
        this.onsetsravail = null;

        this.onsethodobbox = null;
        this.ondrawvwp = null;
        this.onscreenshot = null;

        this.is_animating = false;
        this._is_animation_paused = true;
        this._is_hodo_selecting = false;
        this._anim_timer = null;
        this._anim_intv = 500;

        this._storm_motion = 'brm';
        this._surface_wind = 'metar';
        this._metars = null;

        this._origin = 'ground';

        this._boundary = 'none';

        // TODO: These might not need to be instance variables (only local to check_file_times), but I'm confused about Javascript variable scoping ...
        this._expected_new_frames = 0;
        this._new_frames_loaded = 0;
        this._want_latest_frame = true;

        this.frame_list = new Map();
        this._radar = null;

        this.update_frame_cap();
    }

    trim_frames(update_ui) {
        if (update_ui === undefined) {
            update_ui = true;
        }

        this.frame_list.forEach((frame, fname) => {
            if (this.frame_list.size > this._n_frame_cap) {
                this.frame_list.delete(fname);
            }
        });

        if (update_ui) {
            // Update the UI
            this._update_ui_frame_list();
        }
    }

    check_file_times(radar_id, is_refresh) {
        if (this.onrefreshstart !== null) {
            this.onrefreshstart();
        }

        if (this._radar !== null && this._radar != radar_id) {
            this.frame_list.clear();
            this.pause_animation();
        }
        this._radar = radar_id;

        this._want_latest_frame = (this.frame_list.size == 0 || Array.from(this.frame_list.values()).findIndex(f => f['status'] == 'active') == this.frame_list.size - 1);

        check_files(radar_id, this._age_limit).then(file_times => {
            // Check to see if this is still the radar we're looking for (user might have changed it while we were waiting for data).
            if (this._radar != file_times['radar']) {
                return;
            }

            file_times = file_times['times'];
            file_times = Object.entries(file_times).sort(([fn1, dt1], [fn2, dt2]) => compare_dt(dt1, dt2));
            file_times = file_times.slice(-this._n_frame_cap);

            // Add new frames to frame list
            file_times.forEach(([file_name, dt]) => {
                if (!this.frame_list.has(file_name)) {
                    this.frame_list.set(file_name, {'dt': dt, 'status': 'notloaded'});
                }
            });

            const dt_cutoff = moment.utc().subtract(this._age_limit, 'seconds');

            // Sort frame list by time and delete old frames
            this.frame_list = new Map([...this.frame_list.entries()].sort(([fn1, f1], [fn2, f2]) => compare_dt(f1['dt'], f2['dt']))
                                      .filter(([fn, f]) => f['dt'].isSameOrAfter(dt_cutoff)));

            // Enforce the frame cap
            this.trim_frames(false);

            // If we removed the active frame, make the latest frame the new active frame
            if (Array.from(this.frame_list.values()).findIndex(f => f['status'] == 'active') == -1) {
                var frame = Array.from(this.frame_list.values()).reverse().find(f => f['status'] != 'notloaded');
                if (frame !== undefined) {
                    this._set_active_frame(frame);
                    if (this.ondrawvwp !== null) {
                        this.ondrawvwp(frame['data']);
                    }
                }

                this._want_latest_frame = true;
            }

            // Update the UI
            this._update_ui_frame_list();

            this._new_frames_loaded = 0;
            this._expected_new_frames = 0;

            this._expected_new_frames = Array.from(this.frame_list.values()).filter(frame => frame['status'] == 'notloaded').length;

            // Load new frames
            Array.from(this.frame_list.entries()).filter(([file_name, frame]) => frame['status'] == 'notloaded').reverse().forEach(([file_name, frame]) => {
                var id = file_name.substring(3);
                var dt_str = frame['dt'].format(this._dt_fmt);

                const _debug_missing_frames = false;
                const _debug = _debug_missing_frames && (file_name == this.frame_list.keys().next().value);
                const activate_last_frame = !is_refresh || this._want_latest_frame

                console.log('Downloading vwp at ' + frame['dt'].format(this._dt_format));
                VWP.from_server(radar_id, id, false, false, _debug).then(vwp => {
//                  let frame = this.frame_list.get(file_name);
                    if (this._radar == vwp.radar_id) {
                        // Check to see if this is still the radar we're looking for (user might have changed it while we were waiting for data).
                        this._set_frame_vwp_data(vwp, frame, activate_last_frame);
                    }

                    this._new_frames_loaded++;
                }).catch(error => {
                    console.error(error);
                    this._expected_new_frames--;
                }).finally(() => {
                    // Check to see if we've loaded all the frames we expect from this update and cancel the refresh animation if so.
                    if (this._new_frames_loaded >= this._expected_new_frames) {
                        if (this.onrefreshend !== null) {
                            this.onrefreshend();
                        }
                    }
                });
            });

            // If there are no new frames, stop the animation refresh
            if (this._expected_new_frames == 0) {
                if (this.onrefreshend !== null) {
                    this.onrefreshend();
                }
            }

            // If there are no data at all (e.g. radar is down), reset the hodograph
            if (this.frame_list.size == 0) {
                if (this.onsethodobbox !== null) {
                    this.onsethodobbox(null);
                }
            }
        });
    }

    set_local_files(local_files) {
        this.frame_list.clear();
        local_files.filter(file => file.status == 'ok' || file.status == 'warning').forEach(file => {
            this.frame_list.set(file.name, {'status': 'loaded'});

            let frame = this.frame_list.get(file.name);
            frame['dt'] = file.vwp.radar_dt;

            this._set_frame_vwp_data(file.vwp, frame, true);
        });

        if (local_files.length == 0) {
            this._update_ui_frame_list(false);
            if (this.onsethodobbox !== null) {
                this.onsethodobbox(null);
            }
        }
    }

    set_metar_obs(metars) {
        this._metars = metars;

        if (this.frame_list.size > 0) {
            this.change_surface_wind(this._surface_wind);
        }
    }

    set_anim_time(dt) {
        var frame = Array.from(this.frame_list.values()).find(f => (f['dt'].isSame(dt)));

        this.stop_animation();

        this._set_active_frame(frame);
        this._update_ui_frame_list();

        // Replace with draw_active_frame?
        if (this.ondrawvwp !== null) {
            this.ondrawvwp(frame['data']);
        }
    }

    _set_active_frame(frame) {
        this.frame_list.forEach(function(f) {
            if (f['status'] != 'notloaded') {
                f['status'] = 'loaded';
            }
        });

        frame['status'] = 'active';
    }

    toggle_animation() {
        if (!this.is_animating) {
            this.start_animation();
        }
        else {
            this.stop_animation();
        }
    }

    start_animation() {
        this._is_hodo_selecting = false;
        this._is_animation_paused = false;

        var advance_frame = (function() {
            var frame;
            var anim_idx;

            var has_some_loaded = this.frame_list.size > 0 && Array.from(this.frame_list.values()).map(f => f['status'] != 'notloaded').reduce((s1, s2) => s1 || s2);

            if (has_some_loaded && !this._is_animation_paused) {
                anim_idx = Array.from(this.frame_list.values()).findIndex(f => (f['status'] == 'active'));
                // Increment animation counter
                do {
                    anim_idx = (anim_idx + 1) % this.frame_list.size;
                    frame = Array.from(this.frame_list.values())[anim_idx];
                } while (frame['status'] == 'notloaded');
            }
            else {
                this._is_animation_paused = true;
                return;
            }

            // Update UI
            this._set_active_frame(frame);
            this._update_ui_frame_list();

            // Draw VWP
            if (this.ondrawvwp !== null) {
                this.ondrawvwp(frame['data']);
            }

            // Set timer for next frame
            var intv = this._anim_intv;
            if (anim_idx == this.frame_list.size - 1) {
                intv *= 2.5;
            }

            this._anim_timer = window.setTimeout(advance_frame, intv);
        }).bind(this);

        advance_frame();
        this.is_animating = true;
        if (this.onanimationplay !== null) {
            this.onanimationplay();
        }
    }

    pause_animation(is_hodo_selection) {
        if (is_hodo_selection === undefined) {
            is_hodo_selection = false;
        }
        this._is_animation_paused = true;
        this._is_hodo_selecting = is_hodo_selection;
    }

    stop_animation() {
        if (this._is_hodo_selecting) {
            return;
        }

        window.clearTimeout(this._anim_timer);
        this.is_animating = false;
        this._is_animation_paused = true;
        if (this.onanimationpause !== null) {
            this.onanimationpause();
        }
    }

    animation_speed_up() {
        this._anim_intv /= 1.5;
    }

    animation_speed_down() {
        this._anim_intv *= 1.5;
    }

    screenshot() {
        var frame = Array.from(this.frame_list.values()).find(f => f['status'] == 'active');

        if (frame !== undefined) {
            if (this.onscreenshot === null) {
                return;
            }

            var img_data_url = this.onscreenshot(frame['data']).toDataURL();

            var header = "<head><title>VWP Image</title></head>";
            var iframe = "<body><img width='100%' src='" + String(img_data_url) + "'></body>"
            var win = window.open();
            win.document.open();
            win.document.write(header + iframe);
            win.document.location = '#';
            win.document.close();
        }
    }

    make_gif() {
        if (this.onscreenshot === null) {
            return;
        }

        var gif = new GIF({workers: 4, workerScript: 'js/gifjs/gif.worker.js', quality: 10});
        var n_frames = 0;
        var anim_intv = this._anim_intv;

        var gif_frames = this.frame_list.forEach((function(frame) {
            if (frame['status'] != 'notloaded') {
                n_frames++;

                var canvas = this.onscreenshot(frame['data'], 3);

                var delay = anim_intv;
                if (n_frames == this.frame_list.size) {
                    delay *= 2.5;
                }
                gif.addFrame(canvas, {delay: delay});
            }
        }).bind(this))

        if (n_frames > 0) {
            var header = "<head>";
            header += "<title>VWP GIF [Rendering ...]</title>";
            header += "<style>";
            header += "body { font-family: Trebuchet MS; }";
            header += "@keyframes loading { from {transform: translate(-200px, 0) }; to {transform: translate(0, 0)} }";
            header += "#bar { width: calc(100% + 200px); height: 50px; background: repeating-linear-gradient(90deg, #aacccc, #aacccc 100px, #ffffff 100px, #ffffff 200px); animation: loading 1s linear 0s infinite; }";
            header += "h1 { text-align: center; }";
            header += "#container {width: 100%; position: fixed; top: 50%; transform: translate(0, -80%)}";
            header += "</style>";
            header += "</head>";
            var body = "<body><div id='container'><h1>Rendering GIF ...</h1><div id='bar'>&nbsp;</div></div></body>"
            var win = window.open();
            win.document.open();
            win.document.write(header + body);
            win.document.close();

            gif.on('finished', function(blob) {
                var reader = new FileReader();
                reader.onloadend = function() {
                    var header = "<head><title>VWP GIF</title></head>";
                    var iframe = "<body><img width='100%' src='" + String(reader.result) + "'></body>"

                    win.document.open();
                    win.document.write(header + iframe);
                    win.document.location = '#';
                    win.document.close();
                }

                reader.readAsDataURL(blob);
            });

            gif.render();
        }
    }

    change_surface_wind(new_vec, redraw) {
        if (redraw === undefined) {
            redraw = true;
        }

        this.frame_list.forEach((function(frame) {
            if (frame['status'] != 'notloaded') {
                var vwp = frame['data'];
                if (new_vec == 'metar') {
                    if (this._metars !== null) {
                        var metar = this._metars.slice().reverse().find(m => m['time'].isBefore(vwp.radar_dt));
                        if (metar === undefined) {
                            vwp.change_surface_wind('none');
                        }
                        else {
                            vwp.change_surface_wind([metar['wdir'], metar['wspd']], metar['id'] + " " + metar['time'].format("HHmm") + " UTC");
                        }
                    }
                    else {
                        vwp.change_surface_wind('none');
                    }
                }
                else {
                    vwp.change_surface_wind(new_vec, 'User');
                }
            }
        }).bind(this));

        this._surface_wind = new_vec;

        if (redraw) {
            this.draw_active_frame();
        }
    }

    change_storm_motion(new_vec, redraw) {
        if (redraw === undefined) {
            redraw = true;
        }

        this.frame_list.forEach(function(frame) {
            if (frame['status'] != 'notloaded') {
                frame['data'].change_storm_motion(new_vec);
            }
        });

        this._storm_motion = new_vec;
        this._update_ui_origin_selection();
        this._update_hodo_bbox();

        if (redraw) {
            this.draw_active_frame();
        }
    }

    change_origin(org, redraw) {
        if (redraw === undefined) {
            redraw = true;
        }

        this.frame_list.forEach(function(frame) {
            if (frame['status'] != 'notloaded') {
                frame['data'].change_origin(org);
            }
        });

        this._origin = org;
        this._update_hodo_bbox();

        if (redraw) {
            this.draw_active_frame();
        }
    }

    change_boundary(new_vec, redraw) {
        if (redraw === undefined) {
            redraw = true;
        }

        this.frame_list.forEach(frame => {
            if (frame['status'] != 'notloaded') {
                frame['data'].change_boundary(new_vec);
            }
        });

        this._boundary = new_vec;

        if (redraw) {
            this.draw_active_frame();
        }
    }

    draw_active_frame() {
        if (this.frame_list.size > 0) {
            var frame = Array.from(this.frame_list.values()).find(f => f['status'] == 'active');
            if (frame !== undefined) {
                if (this.ondrawvwp !== null) {
                    this.ondrawvwp(frame['data']);
                }
            }
            else {
                if (this.ondrawvwp !== null) {
                    this.ondrawvwp(null);
                }
            }
        }
        else {
            if (this.ondrawvwp !== null) {
                this.ondrawvwp(null);
            }
        }
    }

    update_frame_cap() {
        const new_n_frame_cap = get_media() == 'mobile' ? 8 : 99;
        const need_more = new_n_frame_cap > this._n_frame_cap;
        const need_trim = new_n_frame_cap < this._n_frame_cap;

        this._n_frame_cap = new_n_frame_cap;

        if (this.frame_list.size > 0) {
            if (need_more) {
                this.check_file_times(this._radar, true);
            }
            else if (need_trim) {
                this.trim_frames();
            }
        }
    }

    _update_ui_origin_selection() {
        var have_sm = Array.from(this.frame_list.values()).filter(f => f['status'] != 'notloaded').map(f => f['data'].has_sm_vec());
        if (have_sm.length == 0) {
            return;
        }

        const sr_is_avail = have_sm.reduce((a, b) => a && b);
        if (this.onsetsravail !== null) {
            this.onsetsravail(sr_is_avail);
        }
    }

    _update_hodo_bbox() {
        var bboxes = Array.from(this.frame_list.values()).filter(f => f['status'] != 'notloaded').map(f => f['data'].get_bbox());
        if (bboxes.length == 0) {
            if (this.onsethodobbox !== null) {
                this.onsethodobbox(null);
            }
        }
        else {
            const bbox = bboxes.reduce(BBox.union);
            if (bbox.lbx === undefined || bbox.ubx === undefined || bbox.lby === undefined || bbox.uby === undefined) {
                if (this.onsethodobbox !== null) {
                    this.onsethodobbox(null);
                }
            }
            else {
                const ctr_u = (bbox.lbx + bbox.ubx) / 2;
                const ctr_v = (bbox.lby + bbox.uby) / 2;

                const side = Math.max(bbox.ubx - bbox.lbx, bbox.uby - bbox.lby);
                const min_u = ctr_u - side / 2;
                const min_v = ctr_v - side / 2;
                const max_u = ctr_u + side / 2;
                const max_v = ctr_v + side / 2;

                if (this.onsethodobbox !== null) {
                    this.onsethodobbox(new BBox(min_u, min_v, max_u, max_v));
                }
            }
        }
    }

    _update_ui_frame_list(expect_data) {
        if (this.onnewframelist !== null) {
            this.onnewframelist(Array.from(this.frame_list.values()), expect_data);
        }
    }

    _set_frame_vwp_data(vwp, frame, activate_last_frame) {

        vwp.change_storm_motion(this._storm_motion);
        vwp.change_origin(this._origin);

        frame['status'] = 'loaded';
        frame['dt'] = vwp.radar_dt;
        frame['data'] = vwp;

        this.change_surface_wind(this._surface_wind, false);
        this.change_boundary(this._boundary, false);
        this._update_ui_origin_selection();
        this._update_hodo_bbox();

        // Update hodograph
        if (activate_last_frame) {
            const [latest_fn, latest_frame] = Array.from(this.frame_list.entries()).filter(([fn, frame]) => frame['status'] != 'notloaded').reverse()[0]
            this._set_active_frame(latest_frame);
        }

        this.draw_active_frame();

        // Update UI
        this._update_ui_frame_list();

        // Restart animation if it's paused
        if (this.is_animating && this._is_animation_paused && !this._is_hodo_selecting) {
            this.start_animation();
        }
    }
}

function check_files(radar_id, age_limit) {
    var root_url = $('#root_url').val();

    if (!window.location.hostname.includes('www')) {
        root_url = root_url.replace('www.', '');
    }

    return (new Promise((resolve, reject) => {
        // Strictly speaking, wrapping it like this isn't necessary, but I don't want to have to care whether I need to use
        //  a jQuery promise or a built-in promise elsewhere
        $.getJSON(root_url + "/vad/get_radar_times.php?radar=" + radar_id + '&age=' + age_limit).done(resolve).fail(reject);
    })).then(json => {
        for (fname in json['times']) {
            json['times'][fname] = moment.utc(json['times'][fname]);
        }
        return json;
    });
}
