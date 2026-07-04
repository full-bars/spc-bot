import logging
from typing import Any, Dict, Tuple, Union

import numpy as np

logger = logging.getLogger("spc_bot")

# Try to import Rust implementations; fall back to Python
try:
    from spc_rust_core import (
        clip_profile as _clip_profile_rust,
    )
    from spc_rust_core import (
        compute_bunkers as _compute_bunkers_rust,
    )
    from spc_rust_core import (
        compute_crit_angl as _compute_crit_angl_rust,
    )
    from spc_rust_core import (
        compute_dtm as _compute_dtm_rust,
    )
    from spc_rust_core import (
        compute_shear_mag as _compute_shear_mag_rust,
    )
    from spc_rust_core import (
        compute_sr_flow as _compute_sr_flow_rust,
    )
    from spc_rust_core import (
        compute_srh as _compute_srh_rust,
    )
    try:
        from spc_rust_core import (
            compute_all_parameters as _compute_all_rust,
        )
    except ImportError:
        _compute_all_rust = None
    _rust_available = True
except ImportError:
    _rust_available = False

def vec2comp(wdir: Union[float, np.ndarray], wspd: Union[float, np.ndarray]) -> Tuple[Union[float, np.ndarray], Union[float, np.ndarray]]:
    u = -wspd * np.sin(np.radians(wdir))
    v = -wspd * np.cos(np.radians(wdir))
    return u, v

def comp2vec(u: Union[float, np.ndarray], v: Union[float, np.ndarray]) -> Tuple[Union[float, np.ndarray], Union[float, np.ndarray]]:
    vmag = np.hypot(u, v)
    vdir = 90 - np.degrees(np.arctan2(-v, -u))
    vdir = np.where(vdir < 0, vdir + 360, vdir)
    vdir = np.where(vdir >= 360, vdir - 360, vdir)
    return vdir, vmag

def interp(u: np.ndarray, v: np.ndarray, altitude: np.ndarray, hght: float) -> Tuple[float, float]:
    u_hght = np.interp(hght, altitude, u, left=np.nan, right=np.nan)
    v_hght = np.interp(hght, altitude, v, left=np.nan, right=np.nan)
    return float(u_hght), float(v_hght)


def _clip_profile(prof: np.ndarray, alt: np.ndarray, clip_alt: float, intrp_prof: float) -> np.ndarray:
    if _rust_available:
        try:
            return np.array(_clip_profile_rust(_to_list(prof), _to_list(alt), clip_alt, intrp_prof))
        except Exception as e:
            logger.debug(f"Rust clip_profile failed: {e} — falling back to Python")
    try:
        idx_clip = np.where((alt[:-1] <= clip_alt) & (alt[1:] > clip_alt))[0][0]
    except IndexError:
        return np.nan * np.ones(prof.size)

    prof_clip = prof[:(idx_clip + 1)]
    prof_clip = np.append(prof_clip, intrp_prof)

    return np.array(prof_clip)


def compute_shear_mag(data: Dict[str, Any], hght: float) -> float:
    if _rust_available:
        try:
            return float(_compute_shear_mag_rust(
                _to_list(data['wind_dir']),
                _to_list(data['wind_spd']),
                _to_list(data['altitude']),
                hght
            ))
        except Exception as e:
            logger.debug(f"Rust compute_shear_mag failed: {e} — falling back to Python")
    u, v = vec2comp(data['wind_dir'], data['wind_spd'])
    u_hght, v_hght = interp(u, v, data['altitude'], hght)
    return float(np.hypot(u_hght - u[0], v_hght - v[0]))


def compute_srh_py(data: Dict[str, Any], storm_motion: Tuple[float, float], hght: float) -> float:
    u, v = vec2comp(data['wind_dir'], data['wind_spd'])
    if len(u) < 2 and len(v) < 2:
        return np.nan

    storm_u, storm_v = vec2comp(*storm_motion)

    sru = (u - storm_u) / 1.94
    srv = (v - storm_v) / 1.94

    sru_hght, srv_hght = interp(sru, srv, data['altitude'], hght)
    sru_clip = _clip_profile(sru, data['altitude'], hght, sru_hght)
    srv_clip = _clip_profile(srv, data['altitude'], hght, srv_hght)

    layers = (sru_clip[1:] * srv_clip[:-1]) - (sru_clip[:-1] * srv_clip[1:])
    return float(layers.sum())


def compute_srh(data: Dict[str, Any], storm_motion: Tuple[float, float], hght: float) -> float:
    if _rust_available:
        try:
            return float(_compute_srh_rust(
                _to_list(data['wind_dir']),
                _to_list(data['wind_spd']),
                _to_list(data['altitude']),
                storm_motion[0], storm_motion[1], hght
            ))
        except Exception as e:
            logger.debug(f"Rust compute_srh failed: {type(e).__name__}: {e} — falling back to Python")
    return compute_srh_py(data, storm_motion, hght)


def compute_sr_flow(data: Dict[str, Any], storm_motion: Tuple[float, float], hght_bot: float, hght_top: float) -> float:
    if _rust_available:
        try:
            return float(_compute_sr_flow_rust(
                _to_list(data['wind_dir']),
                _to_list(data['wind_spd']),
                _to_list(data['altitude']),
                storm_motion[0], storm_motion[1],
                hght_bot, hght_top
            ))
        except Exception as e:
            logger.debug(f"Rust compute_sr_flow failed: {e} — falling back to Python")
    u, v = vec2comp(data['wind_dir'], data['wind_spd'])
    storm_u, storm_v = vec2comp(*storm_motion)

    alt = data['altitude']

    layer_alts = np.linspace(hght_bot, hght_top, 50)
    u_layer = np.interp(layer_alts, alt, u, left=np.nan, right=np.nan)
    v_layer = np.interp(layer_alts, alt, v, left=np.nan, right=np.nan)

    sr_u = u_layer - storm_u
    sr_v = v_layer - storm_v
    sr_mag = np.hypot(sr_u, sr_v)

    if np.all(np.isnan(sr_mag)):
        return np.nan

    return float(np.nanmean(sr_mag))


_BUNKERS_OFFSET_KTS = 7.5 * 1.94  # 7.5 m/s lateral offset from mean wind (Bunkers 2000)


def compute_bunkers_py(data: Dict[str, Any]) -> Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]:
    d = _BUNKERS_OFFSET_KTS
    hght = 6

    u, v = vec2comp(data['wind_dir'], data['wind_spd'])
    u_hght, v_hght = interp(u, v, data['altitude'], hght)
    u_clip = _clip_profile(u, data['altitude'], hght, u_hght)
    v_clip = _clip_profile(v, data['altitude'], hght, v_hght)

    mnu6 = u_clip.mean()
    mnv6 = v_clip.mean()

    shru = u_hght - u[0]
    shrv = v_hght - v[0]

    tmp = d / np.hypot(shru, shrv)
    rstu = mnu6 + (tmp * shrv)
    rstv = mnv6 - (tmp * shru)
    lstu = mnu6 - (tmp * shrv)
    lstv = mnv6 + (tmp * shru)

    return (
        tuple(float(x) for x in comp2vec(rstu, rstv)), # type: ignore
        tuple(float(x) for x in comp2vec(lstu, lstv)), # type: ignore
        tuple(float(x) for x in comp2vec(mnu6, mnv6))  # type: ignore
    )


def compute_bunkers(data: Dict[str, Any]) -> Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]:
    if _rust_available:
        try:
            right, left, mean = _compute_bunkers_rust(
                _to_list(data['wind_dir']),
                _to_list(data['wind_spd']),
                _to_list(data['altitude']),
            )
            return (right, left, mean)  # type: ignore
        except Exception as e:
            logger.debug(f"Rust compute_bunkers failed: {type(e).__name__}: {e} — falling back to Python")
    return compute_bunkers_py(data)


def compute_dtm(data: Dict[str, Any]) -> Tuple[float, float]:
    if _rust_available:
        try:
            res_dir, res_mag = _compute_dtm_rust(
                _to_list(data['wind_dir']),
                _to_list(data['wind_spd']),
                _to_list(data['altitude']),
            )
            return float(res_dir), float(res_mag)
        except Exception as e:
            import logging
            logging.getLogger("spc_bot").debug(
                f"Rust compute_dtm failed: {type(e).__name__}: {e} — falling back to Python"
            )
    try:
        u, v = vec2comp(data['wind_dir'], data['wind_spd'])
        alt = data['altitude']

        layer_alts = np.linspace(0, 0.5, 20)
        u_500 = np.interp(layer_alts, alt, u, left=np.nan, right=np.nan)
        v_500 = np.interp(layer_alts, alt, v, left=np.nan, right=np.nan)
        mn_u_500 = np.nanmean(u_500)
        mn_v_500 = np.nanmean(v_500)

        brm, _, _ = compute_bunkers(data)
        brm_u, brm_v = vec2comp(*brm)

        dtm_u = 0.7 * brm_u + 0.3 * mn_u_500
        dtm_v = 0.7 * brm_v + 0.3 * mn_v_500

        res_dir, res_mag = comp2vec(dtm_u, dtm_v)
        return float(res_dir), float(res_mag)
    except Exception:
        return (np.nan, np.nan)


def compute_crit_angl(data: Dict[str, Any], storm_motion: Tuple[float, float]) -> float:
    if _rust_available:
        try:
            return float(_compute_crit_angl_rust(
                _to_list(data['wind_dir']),
                _to_list(data['wind_spd']),
                _to_list(data['altitude']),
                storm_motion[0], storm_motion[1],
            ))
        except Exception as e:
            import logging
            logging.getLogger("spc_bot").debug(
                f"Rust compute_crit_angl failed: {type(e).__name__}: {e} — falling back to Python"
            )
    u, v = vec2comp(data['wind_dir'], data['wind_spd'])
    storm_u, storm_v = vec2comp(*storm_motion)

    u_05km, v_05km = interp(u, v, data['altitude'], 0.5)

    base_u = storm_u - u[0]
    base_v = storm_v - v[0]

    ang_u = u_05km - u[0]
    ang_v = v_05km - v[0]

    len_base = np.hypot(base_u, base_v)
    len_ang = np.hypot(ang_u, ang_v)

    base_dot_ang = base_u * ang_u + base_v * ang_v
    return float(np.degrees(np.arccos(base_dot_ang / (len_base * len_ang))))


def _to_list(arr) -> list:
    return arr.tolist() if isinstance(arr, np.ndarray) else list(arr)


def compute_parameters(data: Dict[str, Any], storm_motion: str) -> Dict[str, Any]:
    # Pre-convert arrays once so Rust functions receive lists without repeated copies.
    if isinstance(data, dict):
        data = {
            **data,
            'wind_dir': _to_list(data['wind_dir']),
            'wind_spd': _to_list(data['wind_spd']),
            'altitude': _to_list(data['altitude']),
        }

    # Fast path: consolidated Rust call — ~1 FFI crossing instead of ~12.
    if _rust_available and _compute_all_rust is not None:
        try:
            result = _compute_all_rust(
                data['wind_dir'], data['wind_spd'], data['altitude'], storm_motion
            )
            sm = result.get('storm_motion', (float('nan'), float('nan')))
            return {
                'bunkers_right': result['bunkers_right'],
                'bunkers_left': result['bunkers_left'],
                'mean_wind': result['mean_wind'],
                'storm_motion': sm,
                'critical': result.get('critical', float('nan')),
                'shear_mag_500m': result.get('shear_mag_500m', float('nan')),
                'shear_mag_1000m': result.get('shear_mag_1000m', float('nan')),
                'shear_mag_3000m': result.get('shear_mag_3000m', float('nan')),
                'shear_mag_6000m': result.get('shear_mag_6000m', float('nan')),
                'srh_500m': result.get('srh_500m', float('nan')),
                'srh_1000m': result.get('srh_1000m', float('nan')),
                'srh_3000m': result.get('srh_3000m', float('nan')),
                'sr_flow_500m': result.get('sr_flow_500m', float('nan')),
                'sr_flow_1000m': result.get('sr_flow_1000m', float('nan')),
                'sr_flow_3000m': result.get('sr_flow_3000m', float('nan')),
                'dtm': (result.get('dtm_dir', float('nan')), result.get('dtm_mag', float('nan'))),
            }
        except Exception as e:
            logger.debug(f"Rust compute_all_parameters failed: {e} — falling back to individual calls")

    params: Dict[str, Any] = {}

    try:
        params['bunkers_right'], params['bunkers_left'], params['mean_wind'] = compute_bunkers(data)
    except (IndexError, ValueError):
        params['bunkers_right'] = (np.nan, np.nan)
        params['bunkers_left'] = (np.nan, np.nan)
        params['mean_wind'] = (np.nan, np.nan)

    if storm_motion.lower() in ['blm', 'left-mover']:
        params['storm_motion'] = params['bunkers_left']
    elif storm_motion.lower() in ['brm', 'right-mover']:
        params['storm_motion'] = params['bunkers_right']
    elif storm_motion.lower() in ['mnw', 'mean-wind']:
        params['storm_motion'] = params['mean_wind']
    else:
        params['storm_motion'] = tuple(int(v) for v in storm_motion.split('/'))

    try:
        params['critical'] = compute_crit_angl(data, params['storm_motion'])
    except (IndexError, ValueError):
        params['critical'] = np.nan

    for hght, key in [(0.5, "shear_mag_500m"), (1, "shear_mag_1000m"), (3, "shear_mag_3000m"), (6, "shear_mag_6000m")]:
        try:
            params[key] = compute_shear_mag(data, hght)
        except (IndexError, ValueError):
            params[key] = np.nan

    for hght in [1, 3]:
        params["srh_%dm" % (hght * 1000)] = compute_srh(data, params['storm_motion'], hght)

    params['srh_500m'] = compute_srh(data, params['storm_motion'], 0.5)

    for bot, top, key in [(0, 0.5, 'sr_flow_500m'), (0, 1, 'sr_flow_1000m'), (0, 3, 'sr_flow_3000m')]:
        try:
            params[key] = compute_sr_flow(data, params['storm_motion'], bot, top)
        except Exception:
            params[key] = np.nan

    try:
        params['dtm'] = compute_dtm(data)
    except Exception:
        params['dtm'] = (np.nan, np.nan)

    return params
