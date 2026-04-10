from __future__ import print_function

import numpy as np

import matplotlib as mpl
mpl.use('agg')
import pylab
from matplotlib.patches import Circle
from matplotlib.lines import Line2D

import json
from datetime import datetime, timedelta

from lib.vad_plotter.params import vec2comp

_seg_hghts = [0, 3, 6, 9, 12, 18]
_seg_colors = ['r', '#00ff00', '#008800', '#993399', 'c']

def _total_seconds(td):
    return td.days * 24 * 3600 + td.seconds + td.microseconds * 1e-6

def _fmt_timedelta(td):
    seconds = int(_total_seconds(td))
    periods = [('dy',86400),('hr',3600),('min',60),('sec',1)]
    strings = []
    for period_name, period_seconds in periods:
        if seconds > period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            strings.append("%s %s" % (period_value, period_name))
    return " ".join(strings)

def _fmt(val, fmt="%d", suffix=""):
    if np.isscalar(val):
        if np.isnan(val): return "--"
    else:
        if np.any(np.isnan(val)): return "--"
    return (fmt % val) + suffix

def _plot_param_table(parameters, sfc_wind_str=None, web=False):
    storm_dir, storm_spd = parameters['storm_motion']
    trans = pylab.gca().transAxes
    ls = 0.028
    x0 = 1.02
    col1 = x0 + 0.11
    col2 = x0 + 0.20
    col3 = x0 + 0.285

    kw  = dict(color='k', fontsize=9, clip_on=False, transform=trans)
    kwb = dict(color='k', fontsize=9, clip_on=False, transform=trans, fontweight='bold')

    def hline(y):
        line = Line2D([x0, x0 + 0.40], [y]*2, color='k', linestyle='-',
                      transform=trans, clip_on=False)
        pylab.gca().add_line(line)

    y = 1.0 - ls * 0.5
    pylab.text(x0 + 0.175, y, "Parameters", ha='center', fontsize=10, fontweight='bold',
               clip_on=False, transform=trans, color='k')
    y -= ls * 0.8
    hline(y)
    y -= ls

    pylab.text(col1, y, "BWD\n(kts)",     ha='center', va='top', fontsize=8, fontweight='bold', clip_on=False, transform=trans, color='k')
    pylab.text(col2, y, "SR Flow\n(kts)", ha='center', va='top', fontsize=8, fontweight='bold', clip_on=False, transform=trans, color='k')
    pylab.text(col3, y, "SRH\n(m²/s²)",  ha='center', va='top', fontsize=8, fontweight='bold', clip_on=False, transform=trans, color='k')
    y -= ls * 2.2

    for label, bkey, fkey, skey in [
        ("0-500 m",  'shear_mag_500m',  'sr_flow_500m',  'srh_500m'),
        ("0-1 km",   'shear_mag_1000m', 'sr_flow_1000m', 'srh_1000m'),
        ("0-3 km",   'shear_mag_3000m', 'sr_flow_3000m', 'srh_3000m'),
    ]:
        pylab.text(x0, y, label, **kwb)
        pylab.text(col1, y, _fmt(parameters.get(bkey, np.nan)), ha='center', **kw)
        pylab.text(col2, y, _fmt(parameters.get(fkey, np.nan)), ha='center', **kw)
        pylab.text(col3, y, _fmt(parameters.get(skey, np.nan)), ha='center', **kw)
        y -= ls

    pylab.text(x0, y, "0-6 km", **kwb)
    pylab.text(col1, y, _fmt(parameters.get('shear_mag_6000m', np.nan)), ha='center', **kw)
    y -= ls * 0.6
    hline(y)
    y -= ls * 1.2

    for label, key in [
        ("Storm Motion (SM):",        'storm_motion'),
        ("Bunkers Left Mover (LM):",  'bunkers_left'),
        ("Bunkers Right Mover (RM):", 'bunkers_right'),
        ("Mean Wind (MEAN):",         'mean_wind'),
    ]:
        d, s = parameters[key]
        pylab.text(x0, y, label, **kwb)
        pylab.text(x0+0.40, y, _fmt(d,"%03d")+"/"+_fmt(s,"%02d")+" kts", ha='right', **kw)
        y -= ls

    dtm_d, dtm_s = parameters.get('dtm', (np.nan, np.nan))
    pylab.text(x0, y, "Deviant Tor Motion (DTM):", **kwb)
    pylab.text(x0+0.40, y, _fmt(dtm_d,"%03d")+"/"+_fmt(dtm_s,"%02d")+" kts", ha='right', **kw)
    y -= ls * 0.6
    hline(y)
    y -= ls * 1.2

    pylab.text(x0, y, "Critical Angle:", **kwb)
    pylab.text(x0+0.40, y, _fmt(parameters.get('critical', np.nan), "%d", "°"), ha='right', **kw)


def _plot_data(data, parameters):
    storm_dir, storm_spd = parameters['storm_motion']
    bl_dir, bl_spd = parameters['bunkers_left']
    br_dir, br_spd = parameters['bunkers_right']
    mn_dir, mn_spd = parameters['mean_wind']
    dtm_dir, dtm_spd = parameters.get('dtm', (np.nan, np.nan))

    u, v = vec2comp(data['wind_dir'], data['wind_spd'])
    alt = data['altitude']

    storm_u, storm_v = vec2comp(storm_dir, storm_spd)
    bl_u, bl_v = vec2comp(bl_dir, bl_spd)
    br_u, br_v = vec2comp(br_dir, br_spd)
    mn_u, mn_v = vec2comp(mn_dir, mn_spd)
    dtm_u, dtm_v = vec2comp(dtm_dir, dtm_spd)

    seg_idxs = np.searchsorted(alt, _seg_hghts)
    try:
        seg_u = np.interp(_seg_hghts, alt, u, left=np.nan, right=np.nan)
        seg_v = np.interp(_seg_hghts, alt, v, left=np.nan, right=np.nan)
        ca_u  = np.interp(0.5, alt, u, left=np.nan, right=np.nan)
        ca_v  = np.interp(0.5, alt, v, left=np.nan, right=np.nan)
    except ValueError:
        seg_u = np.nan * np.array(_seg_hghts)
        seg_v = np.nan * np.array(_seg_hghts)
        ca_u = ca_v = np.nan

    mkr_z = np.arange(16)
    try:
        mkr_u = np.interp(mkr_z, alt, u, left=np.nan, right=np.nan)
        mkr_v = np.interp(mkr_z, alt, v, left=np.nan, right=np.nan)
    except ValueError:
        mkr_u = mkr_v = np.nan * mkr_z

    for idx in range(len(_seg_hghts) - 1):
        idx_start = seg_idxs[idx]
        idx_end   = seg_idxs[idx + 1]
        c = _seg_colors[idx]

        if not np.isnan(seg_u[idx]):
            pylab.plot([seg_u[idx], u[idx_start]], [seg_v[idx], v[idx_start]], '-', color=c, linewidth=1.5)

        if idx_start < len(data['rms_error']) and data['rms_error'][idx_start] == 0.:
            pylab.plot(u[idx_start:idx_start+2], v[idx_start:idx_start+2], '--', color=c, linewidth=1.5)
            pylab.plot(u[idx_start+1:idx_end],   v[idx_start+1:idx_end],   '-',  color=c, linewidth=1.5)
        else:
            pylab.plot(u[idx_start:idx_end], v[idx_start:idx_end], '-', color=c, linewidth=1.5)

        if not np.isnan(seg_u[idx + 1]):
            pylab.plot([u[idx_end-1], seg_u[idx+1]], [v[idx_end-1], seg_v[idx+1]], '-', color=c, linewidth=1.5)

        for upt, vpt, rms in list(zip(u, v, data['rms_error']))[idx_start:idx_end]:
            pylab.gca().add_patch(Circle((upt, vpt), np.sqrt(2)*rms, color=c, alpha=0.05))

    pylab.plot(mkr_u, mkr_v, 'ko', ms=10)
    for um, vm, zm in zip(mkr_u, mkr_v, mkr_z):
        if not np.isnan(um):
            pylab.text(um, vm-0.1, str(zm), va='center', ha='center', color='white', size=6.5, fontweight='bold')

    try:
        pylab.plot([storm_u, u[0]], [storm_v, v[0]], 'c-', linewidth=0.75)
        pylab.plot([u[0], ca_u],   [v[0], ca_v],     'm-', linewidth=0.75)
    except IndexError:
        pass

    if not (np.isnan(bl_u) or np.isnan(bl_v)):
        pylab.plot(bl_u, bl_v, 'ko', markersize=5, mfc='none')
        pylab.text(bl_u+0.5, bl_v-0.5, "LM", ha='left', va='top', color='k', fontsize=10)
    if not (np.isnan(br_u) or np.isnan(br_v)):
        pylab.plot(br_u, br_v, 'ko', markersize=5, mfc='none')
        pylab.text(br_u+0.5, br_v-0.5, "RM", ha='left', va='top', color='k', fontsize=10)
    if not (np.isnan(mn_u) or np.isnan(mn_v)):
        pylab.plot(mn_u, mn_v, 's', color='#a04000', markersize=5, mfc='none')
        pylab.text(mn_u+0.6, mn_v-0.6, "MEAN", ha='left', va='top', color='#a04000', fontsize=10)
    if not (np.isnan(dtm_u) or np.isnan(dtm_v)):
        pylab.plot(dtm_u, dtm_v, 'kv', markersize=6, mfc='none')
        pylab.text(dtm_u+0.5, dtm_v-0.5, "DTM", ha='left', va='top', color='k', fontsize=10)

    smv_is_brm = (storm_u == br_u and storm_v == br_v)
    smv_is_blm = (storm_u == bl_u and storm_v == bl_v)
    smv_is_mnw = (storm_u == mn_u and storm_v == mn_v)
    if not (np.isnan(storm_u) or np.isnan(storm_v)) and not (smv_is_brm or smv_is_blm or smv_is_mnw):
        pylab.plot(storm_u, storm_v, 'k+', markersize=6)
        pylab.text(storm_u+0.5, storm_v-0.5, "SM", ha='left', va='top', color='k', fontsize=10)


def _plot_background(min_u, max_u, min_v, max_v):
    max_ring = int(np.ceil(max(
        np.hypot(min_u, min_v), np.hypot(min_u, max_v),
        np.hypot(max_u, min_v), np.hypot(max_u, max_v)
    )))
    pylab.axvline(x=0, linestyle='-', color='#999999')
    pylab.axhline(y=0, linestyle='-', color='#999999')
    for irng in range(10, max_ring, 10):
        pylab.gca().add_patch(Circle((0.,0.), irng, linestyle='dashed', fc='none', ec='#999999'))
        if irng <= max_u - 10:
            rng_str = "%d kts" % irng if max_u-20 < irng <= max_u-10 else "%d" % irng
            pylab.text(irng+0.5, -0.5, rng_str, ha='left', va='top', fontsize=9, color='#999999',
                       clip_on=True, clip_box=pylab.gca().get_clip_box())


def plot_hodograph(data, parameters, fname=None, web=False, fixed=False, archive=False, sfc_wind_str=None):
    img_title = "%s VWP valid %s" % (data.rid, data['time'].strftime("%d %b %Y %H%M UTC"))
    img_file_name = fname if fname is not None else "%s_vad.png" % data.rid

    u, v = vec2comp(data['wind_dir'], data['wind_spd'])

    if fixed or len(u) == 0:
        ctr_u, ctr_v, size = 20, 20, 120
    else:
        ctr_u = u.mean(); ctr_v = v.mean()
        size = max(u.max()-u.min(), v.max()-v.min()) + 20
        size = max(120, size)

    min_u, max_u = ctr_u-size/2, ctr_u+size/2
    min_v, max_v = ctr_v-size/2, ctr_v+size/2

    now = datetime.utcnow()
    img_age = now - data['time']
    age_cstop = min(_total_seconds(img_age)/(6*3600), 1) * 0.4
    age_color = mpl.cm.get_cmap('hot')(age_cstop)[:-1]
    age_str = "Image created on %s (%s old)" % (now.strftime("%d %b %Y %H%M UTC"), _fmt_timedelta(img_age))

    pylab.figure(figsize=(10, 7.5), dpi=100)
    fig_wid, fig_hght = pylab.gcf().get_size_inches()
    fig_aspect = fig_wid / fig_hght

    axes_left = 0.05
    axes_bot  = 0.05
    axes_hght = 0.9
    axes_wid  = axes_hght / fig_aspect
    pylab.axes((axes_left, axes_bot, axes_wid, axes_hght))

    _plot_background(min_u, max_u, min_v, max_v)
    _plot_data(data, parameters)
    _plot_param_table(parameters, sfc_wind_str=sfc_wind_str, web=web)

    pylab.xlim(min_u, max_u)
    pylab.ylim(min_v, max_v)
    pylab.xticks([])
    pylab.yticks([])

    if not archive:
        pylab.title(img_title, color=age_color)
        pylab.text(0., -0.01, age_str, transform=pylab.gca().transAxes,
                   ha='left', va='top', fontsize=9, color=age_color)
    else:
        pylab.title(img_title)

    if sfc_wind_str:
        pylab.text(0., -0.04, sfc_wind_str, transform=pylab.gca().transAxes,
                   ha='left', va='top', fontsize=8, color='#444444')

    pylab.text(1.0, -0.01, "https://www.autumnsky.us/vad/",
               transform=pylab.gca().transAxes, ha='right', va='top', fontsize=8, color='#888888')

    pylab.savefig(img_file_name, dpi=pylab.gcf().dpi, bbox_inches='tight')
    pylab.close()

    if web:
        bounds = {'min_u':min_u,'max_u':max_u,'min_v':min_v,'max_v':max_v}
        print(json.dumps(bounds))
