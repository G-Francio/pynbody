from . import snapshot,array
from . import family
from . import config
from . import config_parser

import ConfigParser

import numpy as np
#Needed to unpack things
import struct
import sys
import copy
import os.path as path

#This is set here and not in a config file because too many things break 
#if it is not 6
N_TYPE = 6

_type_map = {}
for x in family.family_names() :
    try :
        #At the moment, families can only correspond to one particle type
        pp = int(config_parser.get('gadget-type-mapping',x))
        qq=np.array(pp)
        if (qq >= N_TYPE).any() or (qq < 0).any() :
            raise ValueError,"Type specified for family "+x+" is out of bounds ("+pp+")." 
        _type_map[family.get_family(x)] = pp
    except ConfigParser.NoOptionError :
        pass

_name_map = {}
_rev_name_map = {}
try :
    for a, b in config_parser.items("gadget-name-mapping") :
    #Need to do some changing of the actual specified names, 
    #because not sure how to config_parse things with spaces in them.
        a=a.upper().ljust(4)
        _rev_name_map[a] = b
        _name_map[b] = a
except ConfigParser.NoOptionError :
    pass

def _translate_array_name(name, reverse=False) :
    try :
        if reverse :
            return _rev_name_map[name]
        else :
            return _name_map[name]
    except KeyError :
        return name
    
def gadget_type(fam) :
    #-1 is "all types"
    if fam == None:
        return -1
    else :
        return _type_map[fam]

class GadgetBlock :
    """Class to describe each block.
    Each block has a start, a length, and a length-per-particle"""
    def __init__(self) :
        #Start of block in file
        self.start=0
        #Length of block in file
        self.length=0
        #Bytes per particle in file
        self.partlen=0
        #Data type of block
        self.data_type = np.float32
        #Types of particle this block contains
        self.p_types = np.zeros(N_TYPE,bool)

class GadgetHeader :
    """Describes the header of gadget class files; this is all our metadata, so we are going to store it inline"""
    def __init__(self, data, endian='=') :
        """Takes a byte range, read from a file, creates a header"""
        #Number of particles
        self.npart = np.zeros(N_TYPE, dtype=np.uint32)
        # Mass of each particle type in this file. If zero,
        # particle mass stored in snapshot.
        self.mass = np.zeros(N_TYPE)
        # Time of snapshot
        self.time = 0.
        # Redshift of snapshot
        self.redshift = 0.
        # Boolean to test the presence of star formation
        self.flag_sfr=False
        # Boolean to test the presence of feedback
        self.flag_feedback=False
        # First 32-bits of total number of particles in the simulation
        self.npartTotal=np.zeros(N_TYPE,dtype=np.int32)
        # Boolean to test the presence of cooling
        self.flag_cooling=False
        # Number of files expected in this snapshot
        self.num_files=0
        # Box size of the simulation
        self.BoxSize=0.
        # Omega_Matter. Note this is Omega_DM + Omega_Baryons
        self.Omega0=0.
        # Dark energy density
        self.OmegaLambda=0.
        # Hubble parameter, in units where it is around 70.
        self.HubbleParam=0.
        # Boolean to test whether stars have an age
        self.flag_stellarage=False
        # Boolean to test the presence of metals
        self.flag_metals=False
        # Long word of the total number of particles in the simulation.
        # At least one version of N-GenICs sets this to something entirely different.
        self.NallHW=np.zeros(N_TYPE,dtype=np.int32)
        self.flag_entropy_instead_u=False       # flags that IC-file contains entropy instead of u
        self.flag_doubleprecision=False  # flags that snapshot contains double-precision instead of single precision

        self.flag_ic_info=False
        # flag to inform whether IC files are generated with Zeldovich approximation,
        # or whether they contain 2nd order lagrangian perturbation theory ICs.
        #    FLAG_ZELDOVICH_ICS     (1)   - IC file based on Zeldovich
        #    FLAG_SECOND_ORDER_ICS  (2)   - Special IC-file containing 2lpt masses
        #    FLAG_EVOLVED_ZELDOVICH (3)   - snapshot evolved from Zeldovich ICs
        #    FLAG_EVOLVED_2LPT      (4)   - snapshot evolved from 2lpt ICs
        #    FLAG_NORMALICS_2LPT    (5)   - standard gadget file format with 2lpt ICs
        # All other values, including 0 are interpreted as "don't know" for backwards compatability.
        self.lpt_scalingfactor=0.      # scaling factor for 2lpt initial conditions
        self.endian=endian
        if data == '':
            return
        fmt= endian+"IIIIIIddddddddiiIIIIIIiiddddiiIIIIIIiiif48s"
        if struct.calcsize(fmt) != 256:
            raise Exception, "There is a bug in gadget.py; the header format string is not 256 bytes"
        (self.npart[0], self.npart[1],self.npart[2],self.npart[3],self.npart[4],self.npart[5],
        self.mass[0], self.mass[1],self.mass[2],self.mass[3],self.mass[4],self.mass[5],
        self.time, self.redshift,  self.flag_sfr, self.flag_feedback,
        self.npartTotal[0], self.npartTotal[1],self.npartTotal[2],self.npartTotal[3],self.npartTotal[4],self.npartTotal[5],
        self.flag_cooling, self.num_files, self.BoxSize, self.Omega0, self.OmegaLambda, self.HubbleParam,self.flag_stellarage, self.flag_metals,
        self.NallHW[0], self.NallHW[1],self.NallHW[2],self.NallHW[3],self.NallHW[4],self.NallHW[5],
        self.flag_entropy_instead_u, self.flag_doubleprecision, self.flag_ic_info, self.lpt_scalingfactor,fill) = struct.unpack(fmt, data)
        return
    
    def serialize(self) :
        """This takes the header structure and returns it as a packed string"""
        fmt= self.endian+"IIIIIIddddddddiiIIIIIIiiddddiiIIIIIIiiif"
        #Do not attempt to include padding in the serialised data; the most common use of serialise 
        #is to write to a file and we don't want to overwrite extra data that might be present
        if struct.calcsize(fmt) != 256-48:
            raise Exception, "There is a bug in gadget.py; the header format string is not 256 bytes"
        #WARNING: On at least python 2.6.3 and numpy 1.3.0 on windows, castless code fails with:
        #SystemError: ..\Objects\longobject.c:336: bad argument to internal function
        #This is because self.npart, etc, has type np.uint32 and not int.
        #This is I think a problem with python/numpy, but cast things to ints until I can determine how widespread it is. 
        data=struct.pack(fmt,int(self.npart[0]), int(self.npart[1]),int(self.npart[2]),int(self.npart[3]),int(self.npart[4]),int(self.npart[5]),
        self.mass[0], self.mass[1],self.mass[2],self.mass[3],self.mass[4],self.mass[5],
        self.time, self.redshift,  self.flag_sfr, self.flag_feedback,
        int(self.npartTotal[0]), int(self.npartTotal[1]),int(self.npartTotal[2]),int(self.npartTotal[3]),int(self.npartTotal[4]),int(self.npartTotal[5]),
        self.flag_cooling, self.num_files, self.BoxSize, self.Omega0, self.OmegaLambda, self.HubbleParam,self.flag_stellarage, self.flag_metals,
        int(self.NallHW[0]), int(self.NallHW[1]),int(self.NallHW[2]),int(self.NallHW[3]),int(self.NallHW[4]),int(self.NallHW[5]),
        self.flag_entropy_instead_u, self.flag_doubleprecision, self.flag_ic_info, self.lpt_scalingfactor)
        return data

class GadgetFile :
    """This is a helper class.
    Should only be called by GadgetSnap.
    Contains the block location dictionary for each file.
    To read Gadget 1 format files, use the gadget-1-blocks config entry"""
    def __init__(self, filename) :
        self._filename=filename
        self.blocks = {}
        self.header=GadgetHeader('')
        self.endian=''
        self.format2=True
        t_part = 0
        fd=open(filename, "rb")
        self.check_format(fd)
        #If format 1, load the block definitions.
        if not self.format2 :
            self.block_names = config_parser.get('gadget-1-blocks',"blocks").split(",")
            for n in self.block_names:
                n = (n.upper().ljust(4," "))[0:4]
        while True:
            block=GadgetBlock()
            (name, block.length) = self.read_block_head(fd)
            if block.length == 0 :
                break
            #Do special things for the HEAD block
            if name[0:4] == "HEAD" :
                if block.length != 256:
                    raise IOError, "Mis-sized HEAD block in "+filename
                self.header=fd.read(256)
                if len(self.header) != 256 :
                    raise IOError, "Could not read HEAD block in "+filename
                self.header=GadgetHeader(self.header, self.endian)
                record_size = self.read_block_foot(fd)
                if record_size != 256 :
                    raise IOError, "Bad record size for HEAD in "+filename
                t_part = self.header.npart.sum()
                continue
            #Set the partlen, using our amazing heuristics
            if name[0:4] == "POS " or name[0:4] == "VEL " :
                if block.length == t_part * 24 :
                    block.partlen = 24
                    block.data_type = np.float64
                else :
                    block.partlen = 12
                    block.data_type = np.float32
            elif name[0:4] == "ID  ":
                #Heuristic for long (64-bit) IDs
                if block.length == t_part * 4 :
                    block.partlen = 4
                    block.data_type = np.int32
                else :
                    block.partlen = 8
                    block.data_type = np.int64
            else :
                if block.length == t_part * 8 :
                    block.partlen = 8
                    block.data_type = np.float64
                else :
                    block.partlen = 4
                    block.data_type = np.float32
            block.start = fd.tell()
            # Check for the case where the record size overflows an int.
            # If this is true, we can't get record size from the length and we just have to guess
            # At least the record sizes at either end should be consistently wrong.
            # Better hope this only happens for blocks where all particles are present.
            extra_len = t_part *block.partlen
            if extra_len >= 2**32 :
                fd.seek(extra_len,1)
            else :
                fd.seek(block.length,1)
            record_size = self.read_block_foot(fd)
            if record_size != block.length :
                raise IOError, "Corrupt record in "+filename+" footer for block "+name
            if extra_len >= 2**32 :
                block.length = extra_len
            # Set up the particle types in the block. This also is a heuristic,
            # which assumes that blocks are either fully present or not for a given particle type
            block.p_types = self.get_block_types(block, self.header.npart)
            self.blocks[name[0:4]] = block

        #and we're done.
        fd.close()
        return

    def get_block_types(self,block, npart):
        """ Set up the particle types in the block, with a heuristic,
        which assumes that blocks are either fully present or not for a given particle type"""
        #This function is horrible.
        p_types = np.zeros(N_TYPE,bool)
        if block.length == npart.sum()*block.partlen:
            p_types= np.ones(N_TYPE, bool)
            return p_types
        #Blocks which contain a single particle type
        for n in np.arange(0,N_TYPE) :
            if block.length == npart[n]*block.partlen :
                p_types[n] = True
                return p_types
        #Blocks which contain two particle types
        for n in np.arange(0,N_TYPE) :
            for m in np.arange(0,N_TYPE) :
                if block.length == (npart[n]+npart[m])*block.partlen :
                    p_types[n] = True
                    p_types[m] = True
                    return p_types
        #Blocks which contain three particle types
        for n in np.arange(0,N_TYPE) :
            for m in np.arange(0,N_TYPE) :
                for l in np.arange(0,N_TYPE) :
                    if block.length == (npart[n]+npart[m]+npart[l])*block.partlen :
                        p_types[n] = True
                        p_types[m] = True
                        p_types[l] = True
                        return p_types
        #Blocks which contain four particle types
        for n in np.arange(0,N_TYPE) :
            for m in np.arange(0,N_TYPE) :
                if block.length == (npart.sum() - npart[n]-npart[m])*block.partlen :
                    p_types = np.ones(N_TYPE, bool)
                    p_types[n] = False
                    p_types[m] = False
                    return p_types
        #Blocks which contain five particle type
        for n in np.arange(0,N_TYPE) :
            if block.length == (npart.sum() -npart[n])*block.partlen :
                p_types = np.ones(N_TYPE, bool)
                p_types[n] = False
                return p_types
        raise ValueError, "Could not determine particle types for block"



    def check_format(self, fd):
        """This function reads the first character of a file and, depending on its value, determines
        whether we have a format 1 or 2 file, and whether the endianness is swapped. For the endianness,
        it then determines the correct byteorder string to pass to struct.unpack. There is not string
        for 'not native', so this is more complex than it needs to be"""
        fd.seek(0,0)
        (r,) = struct.unpack('=I',fd.read(4))
        if r == 8 :
            self.endian = '='
            self.format2 = True
        elif r == 134217728 :
            if sys.byteorder == 'little':
                self.endian = '>'
            else :
                self.endian = '<'
            self.format2 = True
        elif r == 65536 :
            if sys.byteorder == 'little':
                self.endian = '>'
            else :
                self.endian = '<'
            self.format2 = False
        elif r == 256 :
            self.endian = '='
            self.format2 = False
        else :
            raise IOError, "File corrupt. First integer is: "+str(r)
        fd.seek(0,0)
        return

    def read_block_foot(self, fd):
        """Unpacks the block footer, into a single integer"""
        record_size = fd.read(4)
        if len(record_size) != 4 :
            raise IOError, "Could not read block footer"
        (record_size,)= struct.unpack(self.endian+'I',record_size)
        return record_size


    def read_block_head(self, fd) :
        """Read the Gadget 2 "block header" record, ie, 8 name, length, 8.
           Takes an open file and returns a (name, length) tuple """
        if self.format2 :
            head=fd.read(5*4)
            #If we have run out of file, we don't want an exception,
            #we just want a zero length empty block
            if len(head) != 5*4 :
                return ("    ",0)
            head=struct.unpack(self.endian+'I4sIII',head)
            if head[0] != 8 or head[3] != 8 or head[4] != head[2]-8 :
                raise IOError, "Corrupt header record. Possibly incorrect file format"
            #Don't include the two "record_size" indicators in the total length count
            return (head[1], head[2]-8)
        else :
            record_size = fd.read(4)
            if len(record_size) != 4 :
                raise IOError, "Ran out of data in "+self._filename+" before block started"
            (record_size,)=struct.unpack(self.endian+'I', record_size)
            try:
                name = self.block_names[0]
            except IndexError:
                raise ValueError, filename+"is format 1, and contains more blocks than are present in blocks.txt, or the default."  
            self.block_names = self.block_names[1:]
            return (name, record_size)

    def get_block(self, name, p_type, p_toread) :
        """Get a particle range from this file, starting at p_start,
        and reading a maximum of p_toread particles"""
        p_read = 0
        cur_block = self.blocks[name]
        parts = self.get_block_parts(name, p_type)
        p_start = self.get_start_part(name, p_type)
        if p_toread > parts :
            p_toread = parts
        fd=open(self._filename, 'rb')
        fd.seek(cur_block.start+cur_block.partlen*p_start,0)
        #This is just so that we can get a size for the type
        dt = np.dtype(cur_block.data_type)
        n_type = p_toread*cur_block.partlen/dt.itemsize
        data=np.fromfile(fd, dtype=cur_block.data_type, count=n_type, sep = '')
        fd.close()
        if self.endian != '=' :
            data=data.byteswap(True)
        return (p_toread, data)

    def get_block_parts(self, name, p_type):
        """Get the number of particles present in a block in this file"""
        if not self.blocks.has_key(name) :
            return 0
        cur_block = self.blocks[name]
        if p_type == -1 :
            return cur_block.length/cur_block.partlen
        else :
            return self.header.npart[p_type]*cur_block.p_types[p_type]

    def get_start_part(self, name, p_type) :
        """Find particle to skip to before starting, if reading particular type"""
        if p_type == -1:
            return 0
        else :
            if not self.blocks.has_key(name) :
                return 0
            cur_block = self.blocks[name]
            return (cur_block.p_types*self.header.npart)[0:p_type].sum()

    def get_block_dims(self, name):
        """Get the dimensionality of the block, eg, 3 for POS, 1 for most other things"""
        if not self.blocks.has_key(name) :
            return 0
        cur_block = self.blocks[name]
        dt = np.dtype(cur_block.data_type)
        return cur_block.partlen/dt.itemsize
    
    #The following functions are for writing blocks back to the file
    def write_block(self, name, p_type, big_data, filename=None) :
        """Write a full block of data in this file. Any particle type can be written. If the particle type is not present in this file, 
        an exception KeyError is thrown. If there are too many particles, ValueError is thrown. 
        big_data contains a reference to the data to be written. Type -1 is all types"""
        try:
            cur_block=self.blocks[name]
        except KeyError:
            raise KeyError, "Block "+name+" not in file "+self._filename
        MinType=np.ravel(np.where(cur_block.p_types))[0]
        MaxType=np.ravel(np.where(cur_block.p_types))[-1]
        parts = self.get_block_parts(name, p_type)
        p_start = self.get_start_part(name, p_type)
        #Have we been given the right number of particles?
        if np.size(big_data) > parts*self.get_block_dims(name):
            raise ValueError, "Space for "+str(parts)+" particles of type "+str(p_type)+" in file "+self._filename+", "+str(np.shape(big_data)[0])+" requested."
        #Do we have the right type?
        dt = np.dtype(cur_block.data_type)
        bt=big_data.dtype
        if bt.kind != dt.kind : 
            raise ValueError, "Data of incorrect type passed to write_block"
        #Open the file
        if filename == None : 
            fd = open(self._filename, "r+b")
        else :
            fd = open(filename, "r+b")
        #Seek to the start of the block
        fd.seek(cur_block.start+cur_block.partlen*p_start,0)
        #Add the block header if we are at the start of a block
        if p_type == MinType  or p_type < 0:
            data=self.write_block_header(name,cur_block.length)
            #Better seek back a bit first.
            fd.seek(-len(data),1)
            fd.write(data)
        
        if self.endian != '=' :
            big_data=big_data.byteswap(True)

        #Actually write the data
        #Make sure to ravel it, otherwise the wrong amount will be written, 
        #because it will also write nulls every time the first array dimension changes.
        d=np.ravel(big_data.astype(dt)).tostring()
        if len(d) != cur_block.length:
            raise Exception
        fd.write(d)

        if p_type == MaxType or p_type < 0:
            data=self.write_block_footer(name,cur_block.length)
            fd.write(data)

        fd.close()

    def add_file_block(self, name, blocksize, partlen=4, dtype=np.float32, p_types=-1):
        """Add a block to the block table at the end of the file. Do not actually write anything"""
        if self.blocks.has_key(name) :
            raise KeyError,"Block "+name+" already present in file. Not adding"
        def st(val):
            return val.start
        #Get last block
        lb=max(self.blocks.values(), key=st)
        #Make new block
        block=GadgetBlock()
        block.start=lb.start+lb.length+6*4 #For the block header, and footer of the previous block
        block.length=blocksize
        block.partlen=partlen
        block.data_type=dtype
        if p_types == -1:
            block.p_types = np.ones(N_TYPE,bool)
        else:
            block.p_types = p_types
        self.blocks[name]=block

    def write_block_header(self, name, blocksize) :
        """Create a string for a Gadget-style block header, but do not actually write it, for atomicity."""
        if self.format2:
            #This is the block header record, which we want for format two files only
            blkheadsize = 4 + 4*1;#1 int and 4 chars
            nextblock = blocksize + 2 * 4;#Relative location of next block; the extra 2 uints are for storing the headers.
            #Write format 2 header header
            head = struct.pack(self.endian+'I4sII',blkheadsize,name,nextblock,blkheadsize)
        #Also write the record size, which we want for all files*/
        head+=self.write_block_footer(name,blocksize)
        return head

    def write_block_footer(self, name, blocksize) :
        """(Re) write a Gadget-style block footer."""
        return struct.pack(self.endian+'I',blocksize)
  
    def write_header(self, head, filename=None) :
        """Write a file header. Overwrites npart in the argument with the npart of the file, so a consistent file is always written."""
        #Overwrite the npart in the passed header with the file header. 
        head.npart=np.array(self.header.npart)
        data=self.write_block_header("HEAD", 256)
        data+=head.serialize()
        #a mode will ignore the file position, and w truncates the file.
        if filename == None : 
            fd = open(self._filename, "r+")
        else :
            try :
                fd = open(filename, "r+")
            except IOError:
                #If we couldn't open it (because it doesn't exist) open it for writing.
                fd = open(filename, "w+")
        fd.seek(0) #Header always at start of file
        #Write header
        fd.write(data)
        #Seek 48 bytes forward, to skip the padding (which may contain extra data)
        fd.seek(48,1)
        data=self.write_block_footer("HEAD", 256)
        fd.write(data)
        fd.close()


class GadgetSnap(snapshot.SimSnap):
    """Main class for reading Gadget-2 snapshots. The constructor makes a map of the locations
    of the blocks, which are then read by _load_array"""
    def __init__(self, filename, only_header=False, must_have_paramfile=False) :

        global config
        super(GadgetSnap,self).__init__()
        self._files = []
        self._filename=filename
        npart = np.empty(N_TYPE)
        #Check whether the file exists, and get the ".0" right
        try:
            fd=open(filename)
        except IOError:
            fd=open(filename+".0")
            #The second time if there is an exception we let it go through
            filename = filename+".0"
        fd.close()
        if filename[-2:] == ".0" :
            self._filename = filename [:-2]
        #Read the first file and use it to get an idea of how many files we are expecting.
        first_file = GadgetFile(filename)
        self._files.append(first_file)
        files_expected = self._files[0].header.num_files
        npart = np.array(self._files[0].header.npart)
        for i in np.arange(1, files_expected):
            filename = filename[:-1]+str(i)
            tmp_file=GadgetFile(filename)
            if not self.check_headers(tmp_file.header, self._files[0].header) :
                print "WARNING: file "+str(i)+" is not part of this snapshot set!"
                continue
            self._files.append(tmp_file)
            npart=npart+tmp_file.header.npart
        #Set up things from the parent class
        self._num_particles = npart.sum()
        #Set up global header
        self.header=copy.deepcopy(self._files[0].header)
        self.header.npart = npart
        #Check and fix npartTotal and NallHW if they are wrong.
        if npart is not self.header.npartTotal+2**32*self.header.NallHW :
            self.header.NallHW = npart/2**32
            self.header.npartTotal = npart - 2**32*self.header.NallHW
            for f in self._files :
                f.header.npartTotal = self.header.npartTotal
                f.header.NallHW = self.header.NallHW

        self._family_slice = {}

        self._loadable_keys = set([])
        self._family_keys=set([])
        self._family_arrays = {}
        self._arrays = {}
        self.properties = {}
        
        #Set up _family_slice
        for x in _type_map :
            max_t=_type_map[x]
#            max_t=max(_type_map[x])
            self._family_slice[x] = slice(npart[0:max_t].sum(),npart[0:max_t+1].sum())
        
        #Set up _loadable_keys
        for f in self._files :
            self._loadable_keys = self._loadable_keys.union(set(f.blocks.keys()))

        #Add default mapping to unpadded lower case if not in config file.
        for nn in self._loadable_keys : 
            mm = nn.lower().strip()
            if not nn in _rev_name_map :
                _rev_name_map[nn] = mm
            if not mm in _name_map :
                _name_map[mm] = nn

        #Use translated keys only
        self._loadable_keys = [_translate_array_name(x, reverse=True) for x in self._loadable_keys]
        #Set up block list, with attached families, as a caching mechanism
        self._block_list = self.get_block_list()
        
        self._decorate()
    
    def loadable_family_keys(self, fam=None) :
        """Return list of arrays which are loadable for specific families, 
        but not for all families."""
        if fam is not None : 
            return [x for x in self._loadable_keys if self._family_has_loadable_array(fam, x)]
        else :
            return [x for x in self._loadable_keys if not self._family_has_loadable_array(fam, x)]


    def loadable_keys(self) :
            return self._loadable_keys

    def _family_has_loadable_array(self, fam, name) :
        """Returns True if the array can be loaded for the specified family.
        If fam is None, returns True if the array can be loaded for all families."""
        if fam is not None :
                return fam in self._block_list[name]
        else :
                return set(self.families()) <= set(self._block_list[name])

    def get_block_list(self):
        """Get list of unique blocks in snapshot, with the types they refer to"""
        b_list = {}
        for f in self._files:
            b_list.update(f.blocks)
        #Setup array references
        #Make all array names lower-case and trim trailing spaces, to match the names
        #used for tipsy snapshots
        out_list={}
        for k,b in b_list.iteritems() :
            b_name = _translate_array_name(k,reverse=True)
            b_types = [ f for f in self.families() if b.p_types[_type_map[f]] ]
            out_list[b_name] = b_types
        return out_list

    def get_block_parts(self, name, family) :
        """Get the number of particles present in a block, of a given type"""
        total=0
        for f in self._files:
            total+=f.get_block_parts(name, gadget_type(family))
        return total

    def check_headers(self, head1, head2) :
        """Check two headers for consistency"""
        if ( head1.time != head2.time or head1.redshift!= head2.redshift or
           head1.flag_sfr != head2.flag_sfr or
           head1.flag_feedback != head2.flag_feedback or
           head1.num_files != head2.num_files or
           head1.BoxSize != head2.BoxSize or
           head1.Omega0 != head2.Omega0 or
           head1.OmegaLambda != head2.OmegaLambda or
           head1.HubbleParam != head2.HubbleParam  or
           head1.flag_stellarage != head2.flag_stellarage or
           head1.flag_metals != head2.flag_metals) :
            return False;
        #Check array quantities
        if((head1.mass != head2.mass).any() or  (head1.npartTotal != head2.npartTotal).any()) :
        #        or head1.NallHW != head2.NallHW)
            return False;
        #  At least one version of N-GenICs writes a header file which
        #  ignores everything past flag_metals (!), leaving it uninitialised.
        #  Therefore, we can't check them.
        return True
    def _get_array_type(self, name) :
        """Get the type for the array given in name"""
        g_name = _translate_array_name(name)
        return self._files[0].blocks[g_name].data_type

    def _get_array_dims(self, name) :
        """Get the dimensions of an array; ie, is it 3d or 1d"""
        g_name = _translate_array_name(name)
        return self._files[0].get_block_dims(g_name)

    def _load_array(self, name, fam=None) :
        """Read in data from a Gadget file.
        If fam != None, loads only data for that particle family"""
        if not self._family_has_loadable_array( fam, name) :
            if fam is None :
                raise KeyError,"Block "+name+" is not available for all families"
            else :
                raise IOError, "No such array on disk"
        #g_name is the internal name
        g_name = _translate_array_name(name)
        p_read = 0
        p_start = 0

        data = array.SimArray([],dtype=self._get_array_type(name))
        ndim = self._get_array_dims(name)

        if ndim == 1:
            dims = (self.get_block_parts(g_name, fam),)
        else:
            dims = (self.get_block_parts(g_name, fam), ndim)
        
        p_type = gadget_type(fam)
        for f in self._files:
            f_read = 0
            f_parts = f.get_block_parts(g_name, p_type)
            if f_parts == 0:
                continue
            (f_read, f_data) = f.get_block(g_name, p_type, f_parts)
            p_read+=f_read
            data=np.append(data, f_data)
        if fam is None :
            self[name] = data.reshape(dims, order='C').view(array.SimArray)
        else :
            self[fam][name] = data.reshape(dims,order='C').view(array.SimArray)

    @staticmethod
    def _can_load(f) :
        """Check whether we can load the file as Gadget format by reading
        the first 4 bytes"""
        try:
            fd=open(f)
        except IOError:
            try:
                fd=open(f+".0")
            except:
                return False
                #If we can't open the file, we certainly can't load it...
        (r,) = struct.unpack('=I',fd.read(4))
        fd.close()
        #First int32 is 8 for a Gadget 2 file, or 256 for Gadget 1, or the byte swapped equivalent.
        if r == 8 or r == 134217728 or r == 65536 or r == 256 :
            return True
        else :
            return False
    
    @staticmethod
    def _write(self, filename=None) :
        """Write an entire Gadget file (actually an entire set of snapshots)."""
        #Call write_header for every file. 
        for f in self._files:
            f.write_header(self.header,filename=filename)
        #Call _write_array for every array.
        all_keys=set(self.loadable_keys()).union(self.keys()).union(self.family_keys())
        for x in all_keys :
            if not self.is_derived_array(x):
                self._write_array(x, filename=filename)
        #If caller is a tipsy file, construct the GadgetFiles, so conversion works.
        #Find some way of specifying format2, so that conversion works
    
    def _write_array(self, array_name, filename=None) :
        """Write a data array back to a Gadget snapshot, splitting it across files"""
        #Make the name a four-character upper case name, possibly with trailing spaces
        g_name = (array_name.upper().ljust(4," "))[0:4]
        family_names = set(self.loadable_family_keys()).union(self.family_keys())
        nfiles=np.size(self._files)
        f_parts=np.zeros(nfiles)
        #Find where each particle goes
        for i in np.arange(0,nfiles):
            f_parts[i] = self._files[i].get_block_parts(g_name, -1)
        #If there is no block corresponding to this name in the file, add it.
        if np.sum(f_parts) == 0:
            #Get p_type
            p_types=np.zeros(N_TYPES,bool)
            if array_name in family_names :
                for fam in self.families():
                    #We get the particle types we want by trying to load all particle types and seeing which ones work
                    if self[fam].has_key(array_name) :
                        p_types[gadget_type(fam)]=True
            ashape=np.shape(self[array_name])
            asize=np.size(self[array_name])
            per_file = ashape[0]/nfiles
            for f in self._files[:-2]:
                f.add_file_block(self[array_name], per_file,ashape[1],dtype=self[array_name].dtype,p_types=p_types)
            self_files[-1].add_file_block(array_name, ashape[0]-(nfiles-1)*per_file,ashape[1])
        #Write the blocks
        s=0
        for i in np.arange(0,nfiles) :
            if array_name in family_names :
                for fam in self.families() :
                    if self[fam].has_key(array_name) :
                        self._files[i].write_block(g_name, -1, self[fam][array_name][s:(s+f_parts[i])])
            else:
                self._files[i].write_block(g_name, -1, self[array_name][s:(s+f_parts[i])])
            s+=f_parts[i]

