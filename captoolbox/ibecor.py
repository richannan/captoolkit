"""
Computes and applies the inverse barometer correction (IBE) to height data.

The IBE data (a 3d-array) must be first generated from the
ERA-Interim sea-level pressure field, using::

    slp2ibe.py

Example:
    To convert ERA-Interim sea-level pressure [Pa] to
    inverse barometer cor [m]:

        python slp2ibe.py -a -b file.nc

    To apply the IB correction to an ASCII file with x,y,t
    in columns 0,1,2:

        python ibecor.py -a -b file.txt

Notes:
    * For ERA-Interim the point interval on the native Gaussian grid is
      about 0.75 degrees.
    * On sufficiently long time scales and away from coastal effects, the
      ocean’s isostatic response is ~1 cm depression of sea level for a
      1 hecto-Pascal (hPa) or milibar (mbar) increase in P_air (Gill, 1982;
      Ponte and others, 1991; Ponte, 1993).
    * The frequency band 0.03<w<0.5 cpd (T=2-33 days) (a.k.a. the "weather
      band") contains most of the variance in P_air. At higher frequencies,
      tides and measurement noise dominate h, and at lower frequencies,
      seasonal and climatological changes in the ice thickness and the
      underlying ocean state dominate the variability. 
    * The IBE correction has generaly large spatial scales.
    * There can be significant trends in P_air on time scales of 1-3 yr.
    * ERA-Interim MSL pressure has time units: hours since 1900-01-01 00:00:0.0

    The sea level increases (decreases) by approximately 1 cm when air
    pressure decreases (increases) by approximately 1 mbar. The inverse
    barometer correction (IBE) that must be subtracted from the sea surface
    height is simply given by:

        h_ibe = (-1/rho g) * (P - P_ref)

    where P_ref is the global "mean" pressure (reference pressure) over the
    ocean (rho is sea water density and g gravity). For most applications,
    P_ref is assumed to be a constant (e.g., 1013.3 mbar).

    See Dorandeu and Le Traon, 1999:

        http://journals.ametsoc.org/doi/full/10.1175/1520-
        0426%281999%29016%3C1279%3AEOGMAP%3E2.0.CO%3B2

    Our correction uses P_ref spatially variant:

        h_ibe(x,y,t) = (-1/rho g) * [P(x,y,t) - P_ref(x,y)]

    where P_ref(x,y) is the climatological mean at each location.

    Several refereces here:

        https://link.springer.com/chapter/10.1007/978-3-662-04709-5_88

    The IBE correction should be applied as:

        h_cor = h - h_ibe

    If the IBE data cube is global (-90 to 90), subset to speed up I/O!
"""
import os
import sys
import h5py
import argparse
import numpy as np
import datetime as dt
import seaborn as sns
from glob import glob
from scipy import ndimage
from collections import OrderedDict
import matplotlib.pyplot as plt


# Default location of IBE file (HDF5)
IBEFILE = 'IBE_antarctica_3h_19900101-20170331.h5'

# Subset IBE data cube. If True, define limits. #NOTE: Only time works!!!
SUBSET = False
t1, t2 = 2002, 2010
x1, x2 = None, None
y1, y2 = None, None

# Default column numbers of x, y, t, z in the ASCII files
XCOL = 0
YCOL = 1
TCOL = 2
ZCOL = 3

# Default variable names of x, y, t, z in the HDF5 files
XVAR = 'lon'
YVAR = 'lat'
TVAR = 't_sec'
ZVAR = 'h_cor'

# Default reference epoch of input seconds
EPOCH = (1970,1,1,0,0,0)


# Pass command-line arguments
parser = argparse.ArgumentParser(
        description='Computes and apply the inverse barometer correction.')

parser.add_argument(
        'files', metavar='files', type=str, nargs='+',
        help='ASCII or HDF5 file(s) to process')

parser.add_argument(
        '-v', metavar=('x','y','t','h'), dest='vnames', type=str, nargs=4,
        help=('variable names of lon/lat/time/height in HDF5 file'),
        default=[XVAR,YVAR,TVAR,ZVAR],)

parser.add_argument(
        '-c', metavar=('0','1','2'), dest='cols', type=int, nargs=3,
        help=('column positions of lon/lat/time/height in ASCII file'),
        default=[XCOL,YCOL,TCOL,ZCOL],)

parser.add_argument(
        '-e', metavar=('Y','M','D','h','m','s'), dest='epoch', type=int, nargs=6,
        help=('reference epoch of input time in secs'),
        default=EPOCH,)

parser.add_argument(
        '-a', dest='apply', action='store_true',
        help=('apply IBE cor instead of saving to separate file'),
        default=False)


args = parser.parse_args()
files = args.files  # list to long <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
vnames = args.vnames
cols = args.cols
epoch = args.epoch
apply_ = args.apply

# In case a string is passed to avoid "Argument list too long"
if len(files) == 1:
    files = glob(files[0])

# Check extension of input files
if files[0].endswith(('.h5', '.hdf5', '.hdf', '.H5')):
    print 'input is HDF5'
    xvar, yvar, tvar, zvar = vnames
else:
    print 'input is ASCII'
    xvar, yvar, tvar, zvar = cols

print 'parameters:'
for arg in vars(args).iteritems(): print arg
print '# of input files:', len(files)


def secs_to_hours(secs, epoch1=(1970,1,1,0,0,0), epoch2=None):
    """
    Convert seconds since epoch1 to hours since epoch2.

    If epoch2 is None, keeps epoch1 as the reference.
    
    """
    epoch1 = dt.datetime(*epoch1)
    epoch2 = dt.datetime(*epoch2) if epoch2 is not None else epoch1
    secs_btw_epochs = (epoch2 - epoch1).total_seconds()
    return (secs - secs_btw_epochs) / 3600.  # subtract time diff


def get_xyt_ascii(fname, xcol, ycol, tcol):
    """Read x,y,t columns from ASCII file."""
    return np.loadtxt(fname, usecols=(xcol,ycol,tcol), unpack=True)


def get_xyt_hdf5(fname, xvar, yvar, tvar):
    """Read x,y,t variables from HDF5 file."""
    with h5py.File(fname) as f:
        return f[xvar][:], f[yvar][:], f[tvar][:]


def get_xyt(fname, xvar, yvar, tvar):
    """
    Read x,y,t data from ASCII or HDF5 file.

    x, y, t can be column number or variable names.

    """
    if isinstance(xvar, str):
        return get_xyt_hdf5(fname, xvar, yvar, tvar)
    else:
        return get_xyt_ascii(fname, xvar, yvar, tvar)


def savehdf5(outfile, data):
    """Save data in a dictionary to HDF5 (1d arrays)."""
    with h5py.File(outfile, 'w') as f:
        [f.create_dataset(key, data=val) for key, val in data.items()]
        f.close()


def interp3d(x, y, z, v, xi, yi, zi, **kwargs):
    """
    Fast 3D interpolation.
    
    Given a 3d-array (a cube) "v" with pixel coordinates "x","y","z"
    (0-, 1-, 2-axis), interpolate values "xi","yi","zi" using linear
    interpolation.

    Additional kwargs are passed on to ``scipy.ndimage.map_coordinates``.

    Note that in the case of "real-world" coordinates, we might have:
    x=time (0-axis), y=latitude (1-axis), z=longitude (2-axis) or
    x=bands, y=rows, z=cols. Example:
    
        interp_pts = interp3d(time, lat, lon, grid, t_pts, y_pts, x_pts)

    See:
    http://stackoverflow.com/questions/21836067/interpolate-3d-volume-
        with-numpy-and-or-scipy
    http://stackoverflow.com/questions/16217995/fast-interpolation-of-
        regularly-sampled-3d-data-with-different-intervals-in-x-y

    """
    def interp_pixels(grid_coords, interp_coords):
        """Map interpolation coordinates to pixel locations."""
        grid_pixels = np.arange(len(grid_coords))
        if np.all(np.diff(grid_coords) < 0):
            grid_coords, grid_pixels = grid_coords[::-1], grid_pixels[::-1]
        return np.interp(interp_coords, grid_coords, grid_pixels)

    orig_shape = np.asarray(xi).shape
    xi, yi, zi = np.atleast_1d(xi, yi, zi)
    for arr in [xi, yi, zi]:
        arr.shape = -1

    output = np.empty(xi.shape, dtype=float)  # to ensure float output
    coords = [interp_pixels(*item) for item in zip([x, y, z], [xi, yi, zi])]
    ndimage.map_coordinates(v, coords, order=1, output=output, **kwargs)

    return output.reshape(orig_shape)


def wrap_to_180(lon):
    """Wrapps longitude to -180 to 180 degrees."""
    lon[lon>180] -= 360.
    return lon


# Get the IBE data (3d array), outside main loop (load only once!)
print 'loading ibe cube ...'
f = h5py.File(IBEFILE)
x_ibe = f['lon'][:]   # [deg]
y_ibe = f['lat'][:]   # [deg]
t_ibe = f['time'][:]  # [hours since 1900-1-1]
z_ibe = f['ibe']#[:]   # ibc(time,lat,lon) [m]. WARNING: large dataset!

# Subset datset for speed
if SUBSET:

    print 'subsetting ibe ...'

    # Filter time
    t_year = (t_ibe/8760.) + 1900  # hours since 1900 -> years
    k, = np.where((t_year >= t1) & (t_year <= t2))
    k1, k2 = k[0], k[-1]+1

    """
    #NOTE: Dosn't work!
    # Filter latitude
    j, = np.where((y_ibe >= y1) & (y_ibe <= y2))
    j1, j2 = j[0], j[-1]+1

    # Filter longitude
    i, = np.where((x_ibe >= x1) & (x_ibe <= x2))
    i1, i2 = i[0], i[-1]+1
    """

    # Subset
    t_ibe = t_ibe[k1:k2]
    z_ibe = z_ibe[k1:k2,:,:]

    """
    #NOTE: Dosn't work!
    y_ibe = y_ibe[j1:j2]
    x_ibe = x_ibe[i1:i2]
    z_ibe = z_ibe[k1:k2,j1:j2,i1:i2]
    """

    #--- Plot (for testing) ---------------------------

    if 0:
        import pandas as pd
        import matplotlib.pyplot as plt
        from mpl_toolkits.basemap import Basemap

        t_year = t_year[k1:k2]
        t_year = (t_year-2007) * 365.25 - 26  # 26(25) Leap days from 1900 to 2007(2000)

        find_nearest = lambda arr, val: (np.abs(arr-val)).argmin()

        if 1:
           # Single grid cell centered on Larsen-C
            j = find_nearest(y_ibe, -67.5)
            i = find_nearest(x_ibe, 297.5-360)
            p = z_ibe[:,j,i]
        else:
            # Single grid cell centered on Brunt
            j = find_nearest(y_ibe, -75.6)
            i = find_nearest(x_ibe, 333.3-360)
            p = z_ibe[:,j,i]

        plt.plot(t_year, p, linewidth=2)
        plt.show()

        sys.exit()

    if 0:
        # Map of Antarctica
        fig = plt.figure()
        ax = plt.gca()

        m = Basemap(projection='spstere', boundinglat=-60, lon_0=180)

        xx, yy = np.meshgrid(x_ibe, y_ibe)
        xx, yy = m(xx, yy)

        # plot data
        grid = z_ibe[10] - z_ibe[10].mean()
        c = m.pcolormesh(xx, yy, grid, edgecolor='k')

        # Plot ice-shelf boundaries
        FILE_SHELF = '/Users/paolofer/data/masks/scripps/scripps_iceshelves_v1_geod.txt'
        FILE_COAST = '/Users/paolofer/data/masks/scripps/scripps_coastline_v1_geod.txt'

        x, y = np.loadtxt(FILE_SHELF, usecols=(0,1), unpack=True, comments='%')
        x, i_uni, i_inv = np.unique(x, return_index=True, return_inverse=True)
        y = y[i_uni]
        x, y = m(x, y)
        plt.scatter(x, y, s=5, c='.5', facecolor='.5', lw=0, rasterized=True, zorder=2)

        # Plot location of time series
        px, py = m(297.5, -67.5)
        plt.scatter(px, py, s=50, c='r', facecolor='.5', lw=0, rasterized=True, zorder=10)
        plt.show()

        sys.exit()

else:

    z_ibe = z_ibe[:]


for infile in files:

    # Get data points to interpolate 
    x, y, t = get_xyt(infile, xvar, yvar, tvar)

    t_orig = t.copy()

    # Convert input data time to IBE time (hours since 1900-1-1)
    print 'converting secs to hours ...'
    t = secs_to_hours(t, epoch1=epoch, epoch2=(1900,1,1,0,0,0))

    # Assert lons are consistent
    x_ibe = wrap_to_180(x_ibe)
    x = wrap_to_180(x)

    # Interpolate x/y/t onto IBE (3d-array)
    print 'interpolating x/y/t onto IBE cube ...'
    h_ibe = interp3d(t_ibe, y_ibe, x_ibe, z_ibe, t, y, x)

    if apply_:

        # Apply and save correction (h_ibe)
        with h5py.File(infile, 'a') as f:
            f[zvar][:] = f[zvar][:] - h_ibe
            f['h_ibe'] = h_ibe

        outfile = os.path.splitext(infile)[0] + '_IBE.h5'  # HDF5
        os.rename(infile, outfile)

    else:

        # Save corrections to separate file (x, y, t, h_ibe)
        d = OrderedDict([(xvar, x), (yvar, y), (tvar, t_orig), ('h_ibe', h_ibe)])

        if isinstance(xvar, str):
            outfile = os.path.splitext(infile)[0] + '_IBE.h5'  # HDF5
            savehdf5(outfile, d)

        else:
            outfile = os.path.splitext(infile)[0] + '_IBE.txt'  # ASCII
            np.savetxt(outfile, np.column_stack(d.values()), fmt='%.6f')

    print 'input  <-', infile
    print 'output ->', outfile