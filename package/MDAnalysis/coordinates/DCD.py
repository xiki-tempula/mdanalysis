# -*- Mode: python; tab-width: 4; indent-tabs-mode:nil; coding=utf-8 -*-
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
#
# MDAnalysis --- http://mdanalysis.googlecode.com
# Copyright (c) 2006-2015 Naveen Michaud-Agrawal, Elizabeth J. Denning, Oliver Beckstein
# and contributors (see AUTHORS for the full list)
#
# Released under the GNU Public Licence, v2 or any higher version
#
# Please cite your use of MDAnalysis in published work:
#
# N. Michaud-Agrawal, E. J. Denning, T. B. Woolf, and O. Beckstein.
# MDAnalysis: A Toolkit for the Analysis of Molecular Dynamics Simulations.
# J. Comput. Chem. 32 (2011), 2319--2327, doi:10.1002/jcc.21787
#


"""DCD trajectory I/O  --- :mod:`MDAnalysis.coordinates.DCD`
============================================================

Classes to read and write CHARMM/LAMMPS DCD binary
trajectories. Trajectories can be read regardless of system-endianness
as this is auto-detected.

The classes in this module are the reference implementations for the
Trajectory API.
"""
import os
import errno
import numpy

import base

import MDAnalysis.core
import MDAnalysis.core.units
from MDAnalysis import NoDataError


class Timestep(base.Timestep):
    #: indices into :attr:`Timestep._unitcell`` to pull out
    #: ``[A, B, C, alpha, beta, gamma]``.
    _ts_order = [0, 2, 5, 4, 3, 1]

    @property
    def dimensions(self):
        """unitcell dimensions (*A*, *B*, *C*, *alpha*, *beta*, *gamma*)

        lengths *A*, *B*, *C* are in the MDAnalysis length unit (Å), and
        angles are in degrees.

        :attr:`dimensions` is read-only because it transforms the
        actual format of the unitcell (which differs between different
        trajectory formats) to the representation described here,
        which is used everywhere in MDAnalysis.

        .. versionchanged:: 0.8.2
           unitcell is interpreted as ``[A, gamma, B, beta, alpha, C]`` instead of
           ``[A, alpha, B, beta, gamma, C]`` (see `Issue 187`_ for a discussion)

        .. _Issue 187: https://code.google.com/p/mdanalysis/issues/detail?id=187
        """

        # Layout of unitcell is [A, alpha, B, beta, gamma, C] --- (originally CHARMM DCD)
        # override for other formats; this strange ordering is kept for historical reasons
        # (the user should not need concern themselves with this)
        ## orig MDAnalysis 0.8.1 and dcd.c (~2004)
        ##return numpy.take(self._unitcell, [0,2,5,1,3,4])

        # recent NAMD (>2.5?), see Issue 187
        return numpy.take(self._unitcell, self._ts_order)

    @dimensions.setter
    def dimensions(self, box):
        """Set unitcell with (*A*, *B*, *C*, *alpha*, *beta*, *gamma*)

        .. versionadded:: 0.8.2
        """
        numpy.put(self._unitcell, self._ts_order, box)


class DCDWriter(base.Writer):
    """Writes to a DCD file

    Typical usage::

       with DCDWriter("new.dcd", u.atoms.numberOfAtoms()) as w:
           for ts in u.trajectory
               w.write_next_timestep(ts)

    Keywords are available to set some of the low-level attributes of the DCD.      
    """
    format = 'DCD'
    units = {'time': 'AKMA', 'length': 'Angstrom'}

    def __init__(self, filename, numatoms, start=0, step=1,
                 delta=MDAnalysis.core.units.convert(1., 'ps', 'AKMA'), dt=None,
                 remarks="Created by DCDWriter", convert_units=None):
        """Create a new DCDWriter

        :Arguments:
         *filename*
           name of output file
         *numatoms*
           number of atoms in dcd file
         *start*
           starting timestep
         *step*
           skip between subsequent timesteps (indicate that *step* MD
           integrator steps (!) make up one trajectory frame); default is 1.
         *delta*
           timestep (MD integrator time step (!), in AKMA units); default is
           20.45482949774598 (corresponding to 1 ps).
         *remarks*
           comments to annotate dcd file
         *dt*
           **Override** *step* and *delta* so that the DCD records that *dt* ps
           lie between two frames. (It sets *step* = 1 and *delta* = ``AKMA(dt)``.)
           The default is ``None``, in which case *step* and *delta* are used.
         *convert_units*
           units are converted to the MDAnalysis base format; ``None`` selects
           the value of :data:`MDAnalysis.core.flags` ['convert_lengths'].
           (see :ref:`flags-label`)

       .. Note::

          The keyword arguments set the low-level attributes of the DCD
          according to the CHARMM format. The time between two frames would be
          *delta* * *step* ! For convenience, one can alternatively supply the
          *dt* keyword (see above) to just tell the writer that it should
          record "There are dt ps between each frame".

        """
        if numatoms == 0:
            raise ValueError("DCDWriter: no atoms in output trajectory")
        elif numatoms is None:
            # probably called from MDAnalysis.Writer() so need to give user a gentle heads up...
            raise ValueError("DCDWriter: REQUIRES the number of atoms in the 'numatoms' argument\n" +
                             " " * len("ValueError: ") +
                             "For example: numatoms=universe.atoms.numberOfAtoms()")
        self.filename = filename
        # convert length and time to base units on the fly?
        self.convert_units = MDAnalysis.core.flags['convert_lengths'] if convert_units is None \
            else convert_units
        self.numatoms = numatoms

        self.frames_written = 0
        self.start = start
        if dt is not None:
            if dt > 0:
                # ignore step and delta
                self.step = 1
                self.delta = MDAnalysis.core.units.convert(dt, 'ps', 'AKMA')
            else:
                raise ValueError("DCDWriter: dt must be > 0, not {}".format(dt))
        else:
            self.step = step
            self.delta = delta
        self.dcdfile = open(self.filename, 'wb')
        self.remarks = remarks
        self._write_dcd_header(self.numatoms, self.start, self.step, self.delta, self.remarks)

    def _dcd_header(self):
        """Returns contents of the DCD header C structure::
             typedef struct {
               fio_fd fd;                 // FILE *
               fio_size_t header_size;    // size_t == sizeof(int)
               int natoms;
               int nsets;
               int setsread;
               int istart;
               int nsavc;
               double delta;
               int nfixed;
               int *freeind;
               float *fixedcoords;
               int reverse;
               int charmm;
               int first;
               int with_unitcell;
             } dcdhandle;

        .. deprecated:: 0.7.5
           This function only exists for debugging purposes and might
           be removed without notice. Do not rely on it.

        """
        # was broken (no idea why [orbeckst]), see Issue 27
        # 'PiiiiiidiPPiiii' should be the unpack string according to the struct.
        #    struct.unpack("LLiiiiidiPPiiii",self._dcd_C_str)
        # seems to do the job on Mac OS X 10.6.4 ... but I have no idea why,
        # given that the C code seems to define them as normal integers
        import struct

        desc = [
            'file_desc', 'header_size', 'natoms', 'nsets', 'setsread', 'istart',
            'nsavc', 'delta', 'nfixed', 'freeind_ptr', 'fixedcoords_ptr',
            'reverse', 'charmm', 'first', 'with_unitcell']
        return dict(zip(desc, struct.unpack("LLiiiiidiPPiiii", self._dcd_C_str)))

    def write_next_timestep(self, ts=None):
        ''' write a new timestep to the dcd file

        *ts* - timestep object containing coordinates to be written to dcd file

        .. versionchanged:: 0.7.5
           Raises :exc:`ValueError` instead of generic :exc:`Exception`
           if wrong number of atoms supplied and :exc:`~MDAnalysis.NoDataError`
           if no coordinates to be written.
        '''
        if ts is None:
            if not hasattr(self, "ts"):
                raise NoDataError("DCDWriter: no coordinate data to write to trajectory file")
            else:
                ts = self.ts
        elif not ts.numatoms == self.numatoms:
            raise ValueError("DCDWriter: Timestep does not have the correct number of atoms")
        unitcell = self.convert_dimensions_to_unitcell(ts).astype(numpy.float32)  # must be float32 (!)
        if not ts._pos.flags.f_contiguous:  # Not in fortran format
            ts = Timestep(ts)  # wrap in a new fortran formatted Timestep
        if self.convert_units:
            pos = self.convert_pos_to_native(ts._pos,
                                             inplace=False)  # possibly make a copy to avoid changing the trajectory
        self._write_next_frame(pos[:, 0], pos[:, 1], pos[:, 2], unitcell)
        self.frames_written += 1

    def convert_dimensions_to_unitcell(self, ts, _ts_order=Timestep._ts_order):
        """Read dimensions from timestep *ts* and return appropriate unitcell.

        .. SeeAlso:: :class:`Timestep`
        """
        unitcell = super(DCDWriter, self).convert_dimensions_to_unitcell(ts)
        # unitcell is A,B,C,alpha,beta,gamma - convert to order expected by low level
        # DCD routines
        return numpy.take(unitcell, _ts_order)

    def close(self):
        """Close trajectory and flush buffers."""
        self._finish_dcd_write()
        self.dcdfile.close()
        self.dcdfile = None

    def __del__(self):
        if hasattr(self, 'dcdfile') and not self.dcdfile is None:
            self.close()


class DCDReader(base.Reader):
    """Reads from a DCD file

    :Data:
        ts
          :class:`~MDAnalysis.coordinates.base.Timestep` object
          containing coordinates of current frame

    :Methods:
        ``dcd = DCD(dcdfilename)``
           open dcd file and read header
        ``len(dcd)``
           return number of frames in dcd
        ``for ts in dcd:``
           iterate through trajectory
        ``for ts in dcd[start:stop:skip]:``
           iterate through a trajectory
        ``dcd[i]``
           random access into the trajectory (i corresponds to frame number)
        ``data = dcd.timeseries(...)``
           retrieve a subset of coordinate information for a group of atoms
        ``data = dcd.correl(...)``
           populate a :class:`MDAnalysis.core.Timeseries.Collection` object with computed timeseries
    """
    format = 'DCD'
    units = {'time': 'AKMA', 'length': 'Angstrom'}

    def __init__(self, dcdfilename, **kwargs):
        self.filename = self.dcdfilename = dcdfilename  # dcdfilename is legacy
        self.dcdfile = None  # set right away because __del__ checks

        # Issue #32: segfault if dcd is 0-size
        # Hack : test here... (but should be fixed in dcd.c)
        stats = os.stat(self.filename)
        if stats.st_size == 0:
            raise IOError(errno.EIO, "DCD file is zero size", self.filename)

        self.dcdfile = open(self.filename, 'rb')
        self.numatoms = 0
        self.numframes = 0
        self.fixed = 0
        self.skip = 1
        self.periodic = False

        self._read_dcd_header()
        self.ts = Timestep(self.numatoms)
        # Read in the first timestep
        self._read_next_timestep()

    def _dcd_header(self):
        """Returns contents of the DCD header C structure::
             typedef struct {
               fio_fd fd;                 // FILE *
               fio_size_t header_size;    // size_t == sizeof(int)
               int natoms;
               int nsets;
               int setsread;
               int istart;
               int nsavc;
               double delta;
               int nfixed;
               int *freeind;
               float *fixedcoords;
               int reverse;
               int charmm;
               int first;
               int with_unitcell;
             } dcdhandle;

        .. deprecated:: 0.7.5
           This function only exists for debugging purposes and might
           be removed without notice. Do not rely on it.

        """
        # was broken (no idea why [orbeckst]), see Issue 27
        # 'PiiiiiidiPPiiii' should be the unpack string according to the struct.
        #    struct.unpack("LLiiiiidiPPiiii",self._dcd_C_str)
        # seems to do the job on Mac OS X 10.6.4 ... but I have no idea why,
        # given that the C code seems to define them as normal integers
        import struct

        desc = [
            'file_desc', 'header_size', 'natoms', 'nsets', 'setsread', 'istart',
            'nsavc', 'delta', 'nfixed', 'freeind_ptr', 'fixedcoords_ptr', 'reverse',
            'charmm', 'first', 'with_unitcell']
        return dict(zip(desc, struct.unpack("LLiiiiidiPPiiii", self._dcd_C_str)))

    def __iter__(self):
        # Reset the trajectory file, read from the start
        # usage is "from ts in dcd:" where dcd does not have indexes
        self._reset_dcd_read()

        def iterDCD():
            for i in xrange(0, self.numframes, self.skip):  # FIXME: skip is not working!!!
                try:
                    yield self._read_next_timestep()
                except IOError:
                    raise StopIteration

        return iterDCD()

    def _read_next_timestep(self, ts=None):
        if ts is None:
            ts = self.ts
        ts.frame = self._read_next_frame(ts._x, ts._y, ts._z, ts._unitcell, self.skip)
        return ts

    def __getitem__(self, frame):
        if (numpy.dtype(type(frame)) != numpy.dtype(int)) and (type(frame) != slice):
            raise TypeError
        if (numpy.dtype(type(frame)) == numpy.dtype(int)):
            if (frame < 0):
                # Interpret similar to a sequence
                frame = len(self) + frame
            if (frame < 0) or (frame >= len(self)):
                raise IndexError
            self._jump_to_frame(frame)  # XXX required!!
            ts = self.ts
            ts.frame = self._read_next_frame(ts._x, ts._y, ts._z, ts._unitcell, 1)  # XXX required!!
            return ts
        elif type(frame) == slice:  # if frame is a slice object
            if not (((type(frame.start) == int) or (frame.start is None)) and
               ((type(frame.stop) == int) or (frame.stop is None)) and
               ((type(frame.step) == int) or (frame.step is None))):
                raise TypeError("Slice indices are not integers")

            def iterDCD(start=frame.start, stop=frame.stop, step=frame.step):
                start, stop, step = self._check_slice_indices(start, stop, step)
                for i in xrange(start, stop, step):
                    yield self[i]

            return iterDCD()

    def timeseries(self, asel, start=0, stop=-1, skip=1, format='afc'):
        """Return a subset of coordinate data for an AtomGroup

        :Arguments:
            *asel*
               :class:`~MDAnalysis.core.AtomGroup.AtomGroup` object
            *start, stop, skip*
               range of trajectory to access, start and stop are inclusive
            *format*
               the order/shape of the return data array, corresponding
               to (a)tom, (f)rame, (c)oordinates all six combinations
               of 'a', 'f', 'c' are allowed ie "fac" - return array
               where the shape is (frame, number of atoms,
               coordinates)
        """
        start, stop, skip = self._check_slice_indices(start, stop, skip)
        if len(asel) == 0:
            raise NoDataError("Timeseries requires at least one atom to analyze")
        if len(format) != 3 and format not in ['afc', 'acf', 'caf', 'cfa', 'fac', 'fca']:
            raise ValueError("Invalid timeseries format")
        atom_numbers = list(asel.indices())
        # Check if the atom numbers can be grouped for efficiency, then we can read partial buffers
        # from trajectory file instead of an entire timestep
        # XXX needs to be implemented
        return self._read_timeseries(atom_numbers, start, stop, skip, format)

    def correl(self, timeseries, start=0, stop=-1, skip=1):
        """Populate a TimeseriesCollection object with timeseries computed from the trajectory

        :Arguments:
            *timeseries*
               :class:`MDAnalysis.core.Timeseries.TimeseriesCollection`
            *start, stop, skip*
               subset of trajectory to use, with start and stop being inclusive
        """
        start, stop, skip = self._check_slice_indices(start, stop, skip)
        atomlist = timeseries._getAtomList()
        format = timeseries._getFormat()
        lowerb, upperb = timeseries._getBounds()
        sizedata = timeseries._getDataSize()
        atomcounts = timeseries._getAtomCounts()
        auxdata = timeseries._getAuxData()
        return self._read_timecorrel(atomlist, atomcounts, format, auxdata, sizedata, lowerb, upperb, start, stop, skip)

    def close(self):
        self._finish_dcd_read()
        self.dcdfile.close()
        self.dcdfile = None

    def Writer(self, filename, **kwargs):
        """Returns a DCDWriter for *filename* with the same parameters as this DCD.

        All values can be changed through keyword arguments.

        :Arguments:
          *filename*
              filename of the output DCD trajectory
        :Keywords:
          *numatoms*
              number of atoms
          *start*
              number of the first recorded MD step
          *step*
              indicate that *step* MD steps (!) make up one trajectory frame
          *delta*
              MD integrator time step (!), in AKMA units
          *dt*
             **Override** *step* and *delta* so that the DCD records that *dt* ps
             lie between two frames. (It sets *step* = 1 and *delta* = ``AKMA(dt)``.)
             The default is ``None``, in which case *step* and *delta* are used.
          *remarks*
              string that is stored in the DCD header [XXX -- max length?]

        :Returns: :class:`DCDWriter`

        .. Note::

           The keyword arguments set the low-level attributes of the DCD
           according to the CHARMM format. The time between two frames would be
           *delta* * *step* !
 
        .. SeeAlso:: :class:`DCDWriter` has detailed argument description
        """
        numatoms = kwargs.pop('numatoms', self.numatoms)
        kwargs.setdefault('start', self.start_timestep)
        kwargs.setdefault('step', self.skip_timestep)
        kwargs.setdefault('delta', self.delta)
        kwargs.setdefault('remarks', self.remarks)
        # dt keyword is simply passed through if provided
        return DCDWriter(filename, numatoms, **kwargs)

    def __del__(self):
        if not self.dcdfile is None:
            self.close()

# Add the c functions to their respective classes so they act as class methods
import _dcdmodule
import new

DCDReader._read_dcd_header = new.instancemethod(_dcdmodule.__read_dcd_header, None, DCDReader)
DCDReader._read_next_frame = new.instancemethod(_dcdmodule.__read_next_frame, None, DCDReader)
DCDReader._jump_to_frame = new.instancemethod(_dcdmodule.__jump_to_frame, None, DCDReader)
DCDReader._reset_dcd_read = new.instancemethod(_dcdmodule.__reset_dcd_read, None, DCDReader)
DCDReader._finish_dcd_read = new.instancemethod(_dcdmodule.__finish_dcd_read, None, DCDReader)
DCDReader._read_timeseries = new.instancemethod(_dcdmodule.__read_timeseries, None, DCDReader)

DCDWriter._write_dcd_header = new.instancemethod(_dcdmodule.__write_dcd_header, None, DCDWriter)
DCDWriter._write_next_frame = new.instancemethod(_dcdmodule.__write_next_frame, None, DCDWriter)
DCDWriter._finish_dcd_write = new.instancemethod(_dcdmodule.__finish_dcd_write, None, DCDWriter)
del (_dcdmodule)

# dcdtimeseries is implemented with Pyrex - hopefully all dcd reading functionality can move to pyrex
import dcdtimeseries
#DCDReader._read_timeseries = new.instancemethod(dcdtimeseries.__read_timeseries, None, DCDReader)
DCDReader._read_timecorrel = new.instancemethod(dcdtimeseries.__read_timecorrel, None, DCDReader)
del (dcdtimeseries)
del (new)
