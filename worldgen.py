from __future__ import print_function

import mcpi
from mcpi import minecraft
from mcpi.vec3 import Vec3
from threading import Thread
import time
import numpy as np
import queue
import threading
from connection import Connection
import random
import os

# Run this code in the terminal to improve performance

# !!!!!!!!!!!!!!!!!!!!!!!!!!!
# GENERATION CODE STARTS HERE
# !!!!!!!!!!!!!!!!!!!!!!!!!!!

# Requirements:
# 1.generator():
#   Input: chunk pos
#     each chunk is 64x128x64 in xyz order
#   Output: must return 2 numpy arrays
#   IMPORTANT:
#     Chunks do not have negative coordinate values.
#     If you want to place a block at y=0, write it at [x, 32, z]
#     Writing at [x, 0, z] is bedrock level
#     generator() should be thread safe. In this example it is not
#     and perlin noise sometimes gives wrong values.
# 2.arguments:
#   Must be defined, can be empty
#   Used to pass global variables to the generator
#   In this example, the noise is passed.
#   Several values can be passed.

import perlin_noise

noise = perlin_noise.PerlinNoise(octaves = 5, seed = 4)
arguments = [noise] # Must be defined

def generator(terrain_chunk_position : Vec3, arguments):
    # 1.Make a giant block array (always needed)
    blocks = np.zeros([64, 128, 64], dtype=np.byte)
    ids = np.zeros([64, 128, 64], dtype=np.byte)
    
    # 2.Unpack arguments (optional, depends on the generator)
    noise = arguments[0]
    
    # 3.Fill it using perlin noise (can be changed to your algorithm)
    for x_offset in range(64):
        for z_offset in range(64):
            offset = Vec3(x_offset,0,z_offset)
            noise_position = terrain_chunk_position * 64 + offset # Get actual position
            
            height = int(32 + 30 * noise([noise_position.x/100.21, noise_position.z/100.21]))
            for y in range(height):
                blocks[x_offset,y,z_offset] = 1 # Place blocks
        
    # 4.Return arrays (always needed)
    return blocks, ids

# !!!!!!!!!!!!!!!!!!!!!!!!!!!
#  GENERATION CODE ENDS HERE
# !!!!!!!!!!!!!!!!!!!!!!!!!!!

# Everything beyond this point is backend code
# It is used to handle the player, build optimising and more.
# Editing it is not advised.

def threadedQuadSetup(work_queue, thread_count=20):
    def threadedQuad(connection, work_queue):
        while True:
            if not work_queue.empty():
                try:
                    cube = work_queue.get(False)
                except:
                    continue
                connection.send(b"world.setBlocks",
                                cube[0], cube[1], cube[2], cube[3])
                # After testing, the queue load is distributed,
                # and all calls are cubes.
                # No bugs here.
                
            elif stop_workers.is_set():    
                connection.close()
                break
    
    # Start threads
    workers = []
    for i in range(thread_count):
        worker = threading.Thread(target = threadedQuad, args = (Connection("localhost", 4711), work_queue))
        worker.start()
        workers.append(worker)
    
    # Stop threads
    for worker in workers:
        worker.join() # Wait for it to stop
        print('Stopped', worker)

def toRelative(coordinates : Vec3):
    global world_offset
    return coordinates + world_offset

def toReal(coordinates : Vec3):
    global world_offset
    return coordinates - world_offset

def build(terrain_chunk_position : Vec3, world_chunk_position : Vec3, work_queue, arguments):
    global generating
    generating += 1
    try:
        chunk_top    = open(f'world/t{terrain_chunk_position.x};{terrain_chunk_position.z}.quad', 'rb')
        chunk_bottom = open(f'world/b{terrain_chunk_position.x};{terrain_chunk_position.z}.quad', 'rb')
        
        data_top    = chunk_top.read()
        data_bottom = chunk_bottom.read()
        
        chunk_top.close()
        chunk_bottom.close()
        
        if len(data_top) == 0 or len(data_bottom) == 0:
            print(f'Empty quad chunk {terrain_chunk_position}. Deleting.')
            # Corrupt file. Remove and raise an error
            os.remove(f'world/t{terrain_chunk_position.x};{terrain_chunk_position.z}.quad')
            os.remove(f'world/b{terrain_chunk_position.x};{terrain_chunk_position.z}.quad')
            
            raise FileNotFoundError
    except FileNotFoundError:
        print(f'Generating chunk {terrain_chunk_position}')
        # Make files
        open(f'world/t{terrain_chunk_position.x};{terrain_chunk_position.z}.quad', 'x').close()
        open(f'world/b{terrain_chunk_position.x};{terrain_chunk_position.z}.quad', 'x').close()
        
        chunk_top    = open(f'world/t{terrain_chunk_position.x};{terrain_chunk_position.z}.quad', 'wb')
        chunk_bottom = open(f'world/b{terrain_chunk_position.x};{terrain_chunk_position.z}.quad', 'wb')
        
        blocks, ids = generator(terrain_chunk_position, arguments)
        
        data_top    = bytes(saveQuad(blocks[:,:64,:], ids[:,:64,:]))
        data_bottom = bytes(saveQuad(blocks[:,64:,:], ids[:,64:,:]))
        
        chunk_top.write(data_top)
        chunk_bottom.write(data_bottom)
        
        chunk_top.close()
        chunk_bottom.close()
    finally:
        pointer_id = threading.get_ident() # Allocate a pointer
        
        quad_pointers[pointer_id] = 0
        loadQuad(toRelative(world_chunk_position * 64), Vec3(64,64,64), data_bottom, work_queue, pointer_id)
        quad_pointers[pointer_id] = 0
        loadQuad(toRelative(world_chunk_position * 64) + Vec3(0,64,0), Vec3(64,64,64), data_top, work_queue, pointer_id)
        
        quad_pointers.pop(pointer_id) # Free the memory
        
    generating -= 1
    
def loadQuad(world_position : Vec3, size : Vec3, data : bytes, work_queue, pointer_id):
    global quad_pointers
    # Quad    = 91 '['
    # No edit = 93 ']'
    if size.x == 0 or size.y == 0 or size.z == 0:
        return
    
    last_byte = data[quad_pointers[pointer_id]]
    quad_pointers[pointer_id] += 1
    
    half_size = size.clone()
    half_size *= 0.5
    half_size.ifloor()
    
    if last_byte == 91:
        for x in range(2):
            for y in range(2):
                for z in range(2):
                    loadQuad(world_position + Vec3(x * half_size.x, y * half_size.y, z * half_size.z), half_size, data, work_queue, pointer_id)
    else:
        if last_byte == 93: # Don't change the quad
            return
        debug = False
        
        block = last_byte
        if debug:
            if block != 0:
                # USE THIS TO DEBUG THE QUAD DECOMPOSITION
                block = 35 # Wool
                size_power = [1,2,4,8,16,32,64].index(size.x) # HACK: ONLY WORKS ON 2^N QUADS!!!
                pos_sum = world_position.x + world_position.y + world_position.z
                last_byte = size_power * 2 + ((pos_sum / size.x) % 2)
        else:
            last_byte = data[quad_pointers[pointer_id]]
        
        quad_pointers[pointer_id] += 1
        
        # Note: No bugs during unpacking found, check generator/saver
        # Add to worker queue
        work_queue.put((world_position, world_position + size - Vec3(1,1,1), block, last_byte))
        
def saveQuad(blocks, ids):
    # Quad    = 91 '['
    # No edit = 93 ']'
    if blocks.shape == (1,1,1):
        return [blocks[0,0,0], ids[0,0,0]]
    
    if 0 in blocks.shape:
        return [93] # Empty because of a zero quad
    
    min_block = blocks.argmin()
    min_id = ids.argmin()
    if min_block == blocks.argmax() and min_id == ids.argmax():
        # All blocks are the same, quad is solved
        return [blocks[0,0,0], ids[0,0,0]]
    
    output = [91] # Start packing a quad
    size_x, size_y, size_z = blocks.shape
    for x in range(2):
        for y in range(2):
            for z in range(2):
                quad = saveQuad(
                    blocks[
                        [0,size_x//2][x]:[size_x//2,size_x][x],
                        [0,size_y//2][y]:[size_y//2,size_y][y],
                        [0,size_z//2][z]:[size_z//2,size_z][z]
                    ], ids[
                        [0,size_x//2][x]:[size_x//2,size_x][x],
                        [0,size_y//2][y]:[size_y//2,size_y][y],
                        [0,size_z//2][z]:[size_z//2,size_z][z]])
                output += quad
    return output

def getPlayerChunk(player):
    position = toReal(player.getTilePos())
    position.x = position.x // 64
    position.z = position.z // 64
    position.y = -1
    return position

def getPlayerFloatChunk(player):
    position = toReal(player.getTilePos())
    position.x = position.x / 64
    position.z = position.z / 64
    position.y = -1
    return position

def setChunk(pos, value, work_queue, arguments, threaded = True):
    global chunk_coords
    if chunk_coords[pos] == value:
        return
    else:
        chunk_coords[pos] = value
        if threaded:
            t = threading.Thread(target = build, args = (value, Vec3(pos[0],0,pos[1]), work_queue, arguments))
            t.start()
        else:
            build(value, Vec3(pos[0],0,pos[1]), work_queue, arguments)

def main(work_queue):
    chunk = getPlayerChunk(mc.player)
    fchunk = getPlayerFloatChunk(mc.player)
    global chunk_coords
    s = work_queue.qsize()
    print(f'Queue size: {s}        ', end = '\r')

    if chunk.x == 0:
        mc.postToChat('Generating new terrain. Please wait.')
        print('Player on X-')
        for offset_x in range(2):
            for z in range(4):
                setChunk((offset_x + 2, z), chunk_coords[(offset_x + 0, z)], work_queue, arguments)
        while generating:
            print(f'Generators: {generating}      ',end = '\r')
        mc.player.setPos(mc.player.getPos() + Vec3(128,0,0))
        print(f'Generators: {generating}      ',end = '\r')
        
    elif 1.25 < fchunk.x < 2.00:
        #mc.postToChat('Generating unseen half')
        for z in range(4):
            setChunk((0, z), chunk_coords[(1, z)] - Vec3(1,0,0), work_queue, arguments)
            setChunk((2, z), chunk_coords[(1, z)] + Vec3(1,0,0), work_queue, arguments)
            
    elif 2.00 < fchunk.x < 2.75:
        #mc.postToChat('Generating unseen half')
        for z in range(4):
            setChunk((1, z), chunk_coords[(2, z)] - Vec3(1,0,0), work_queue, arguments)
            setChunk((3, z), chunk_coords[(2, z)] + Vec3(1,0,0), work_queue, arguments)
                
    elif chunk.x == 3:
        mc.postToChat('Generating new terrain. Please wait.')
        print('Player on X+')
        for offset_x in range(2):
            for z in range(4):
                setChunk((offset_x + 0, z), chunk_coords[(offset_x + 2, z)], work_queue, arguments)
        while generating:
            print(f'Generators: {generating}      ',end = '\r')
        mc.player.setPos(mc.player.getPos() - Vec3(128,0,0))
        print(f'Generators: {generating}      ',end = '\r')
        
    chunk = getPlayerChunk(mc.player)
    fchunk = getPlayerFloatChunk(mc.player)
    
    if chunk.z == 0:
        mc.postToChat('Generating new terrain. Please wait.')
        print('Player on Z-')
        for offset_z in range(2):
            for x in range(4):
                setChunk((x, offset_z + 2), chunk_coords[(x, offset_z + 0)], work_queue, arguments)
        while generating:
            print(f'Generators: {generating}      ',end = '\r')
        mc.player.setPos(mc.player.getPos() + Vec3(0,0,128))
        print(f'Generators: {generating}      ',end = '\r')
        
    elif 1.25 < fchunk.z < 2.00:
        #mc.postToChat('Generating unseen half')
        for x in range(4):
            setChunk((x, 0), chunk_coords[(x, 1)] - Vec3(0,0,1), work_queue, arguments)
            setChunk((x, 2), chunk_coords[(x, 1)] + Vec3(0,0,1), work_queue, arguments)
    elif 2.00 < fchunk.z < 2.75:
        #mc.postToChat('Generating unseen half')
        for x in range(4):
            setChunk((x, 1), chunk_coords[(x, 2)] - Vec3(0,0,1), work_queue, arguments)
            setChunk((x, 3), chunk_coords[(x, 2)] + Vec3(0,0,1), work_queue, arguments)
                
    elif chunk.z == 3:
        mc.postToChat('Generating new terrain. Please wait.')
        print('Player on Z+')
        for offset_z in range(2):
            for x in range(4):
                setChunk((x, offset_z + 0), chunk_coords[(x, offset_z + 2)], work_queue, arguments)
        while generating:
            print(f'Generators: {generating}      ',end = '\r')
        mc.player.setPos(mc.player.getPos() - Vec3(0,0,128))
        print(f'Generators: {generating}      ',end = '\r')
    
mc = minecraft.Minecraft.create()

world_offset = mc.player.getTilePos()
world_offset.y = -64
mc.postToChat(f'Calculating world offset')

# Search min X:
while mc.getBlock(world_offset) != 95: # Border
    world_offset.x -= 1
world_offset.x += 1
mc.postToChat(f'X offset: {world_offset.x}')

# Search min Z:
while mc.getBlock(world_offset) != 95: # Border
    world_offset.z -= 1
world_offset.z += 1
mc.postToChat(f'Z offset: {world_offset.z}')

mc.player.setTilePos(world_offset + Vec3(128,128,128))

# Setting up crucial variables
work_queue = queue.Queue()
global generating
generating = 0
global quad_pointers
quad_pointers = {}

stop_workers = threading.Event()
quad_builder = threading.Thread(target = threadedQuadSetup, args = (work_queue, ))
quad_builder.start()

# Pregenerate chunk coords
mc.postToChat('Generating')
chunk_coords = {}
for x in range(4):
    for z in range(4):
        chunk_coords[(x, z)] = Vec3(-1, 0, -1)
        setChunk((x, z), Vec3(x, 0, z), work_queue, arguments)
while generating:
    print(f'Generators: {generating}',end = '\r')
print(f'Generators: {generating}',end = '\r')
mc.postToChat('Initial terrain generated. Building.')

try:
    while True:
        main(work_queue)
except KeyboardInterrupt:
    print('Stopping all threads')
    stop_workers.set()
    quad_builder.join()